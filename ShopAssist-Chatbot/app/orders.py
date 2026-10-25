"""Code-owned ordering (Stage 3).

The MODEL may only *prepare* a quote (tools.get_price_quote). Everything that
matters afterwards is done here, in code:
  * the customer-facing quote text (itemised, branch, total, YES/NO)
  * reading the customer's YES / NO
  * re-checking price and stock at the moment of YES
  * creating the order and writing the receipt
A quote id is the idempotency key, so a basket can be re-ordered later, and a
double YES can never create two orders.
"""
import json
import re
import secrets

from app import db
from app import session as _session
from app.config import QUOTE_TTL_MINUTES, RESERVATION_HOURS
from app.textfmt import money, wat


# ---------- reading the customer's answer ----------

_YES = {"yes", "yeah", "yep", "yup", "yea", "ok", "okay", "okey", "sure", "confirm",
        "confirmed", "proceed", "correct", "ehen", "oya", "place", "ahead"}
_NO = {"no", "nope", "nah", "cancel", "stop", "never", "abort", "dont", "not"}
_FILLER = {"please", "pls", "plz", "kindly", "go", "it", "the", "this", "that", "order",
           "my", "now", "thanks", "thank", "you", "do", "i", "want", "to", "is", "right",
           "and", "abeg", "sharp", "quote"}


def parse_confirmation(text: str) -> str | None:
    """'yes' / 'no' ONLY when the whole message is a plain answer; anything
    else (numbers, questions, 'yes but make it 3') returns None and goes to the model."""
    if re.search(r"[\d?]", text):
        return None
    words = set(re.findall(r"[a-z]+", text.lower().replace("'", "")))
    if not words or not words <= (_YES | _NO | _FILLER):
        return None
    yes, no = words & _YES, words & _NO
    if yes and no:
        return None
    return "yes" if yes else "no" if no else None


# ---------- building and showing a quote ----------

def build_quote(branch, resolved: list[dict]) -> dict:
    now = _session._now()
    return {
        "id": "Q-" + secrets.token_hex(4).upper(),
        "branch_id": branch["branch_id"],
        "items": [{"product_id": r["item"]["product_id"], "name": r["item"]["name"],
                   "quantity": r["quantity"], "unit_price_kobo": r["item"]["price_kobo"]}
                  for r in resolved],
        "totals": db.compute_totals(resolved),
        "created_at": now,
        "expires_at": now + QUOTE_TTL_MINUTES * 60,
    }


def quote_text(branch, quote: dict) -> str:
    lines = [f"*Order summary — {branch['name']}*"]
    for it in quote["items"]:
        unit = it["unit_price_kobo"]
        lines.append(f"{it['quantity']} × {it['name']} @ {money(unit)} = "
                     f"{money(unit * it['quantity'])}")
    t = quote["totals"]
    if t["tax"]:
        lines.append(f"Tax: {money(t['tax'])}")
    if t["delivery"]:
        lines.append(f"Fee: {money(t['delivery'])}")
    lines += [f"*Total: {money(t['total'])}* (pay at pickup)",
              f"Pickup: {branch['address']}", "",
              f"Reply *YES* to reserve this for pickup (held {RESERVATION_HOURS} hours) "
              f"or *NO* to cancel. This quote is valid for {QUOTE_TTL_MINUTES} minutes."]
    return "\n".join(lines)


def receipt_text(branch, order) -> str:
    lines = [f"*Order reserved at {branch['name']}*",
             f"Order ID: *{order['order_id']}*",
             f"Pickup code: *{order['pickup_code']}*"]
    for it in json.loads(order["items_json"]):
        lines.append(f"{it['quantity']} × {it['name']}")
    lines += [f"Total to pay at pickup: *{money(order['total_kobo'])}*",
              f"Pickup: {branch['address']}",
              f"Held until {wat(order['expires_at'])}. Show your pickup code at the counter."]
    return "\n".join(lines)


# ---------- acting on YES / NO ----------

def cancel_pending(session) -> str:
    session.clear_quote()
    return "Okay, I've cancelled that. Tell me what you'd like instead."


def confirm_pending(session) -> str:
    q = session.pending_quote
    if not q:
        return "There's nothing waiting for confirmation. Tell me what you'd like to order."
    if session.quote_expired():
        session.clear_quote()
        return (f"That quote expired (quotes are valid for {QUOTE_TTL_MINUTES} minutes), "
                "so nothing was reserved. Tell me what you'd like and I'll price it again.")
    branch = db.get_branch(q["branch_id"])
    if not branch or not branch["active"] or q["branch_id"] != session.branch_id:
        session.clear_quote()
        return "That quote was for a different pickup branch, so I've dropped it. What would you like?"

    # Re-check the shelf at the moment of YES: prices and stock may have moved.
    items, problems, price_moved = [], [], False
    for line in q["items"]:
        row = db.get_item(q["branch_id"], line["product_id"])
        if not row:
            problems.append(f"{line['name']} is no longer stocked here.")
        elif row["stock_qty"] < line["quantity"]:
            problems.append(f"only {row['stock_qty']} of {line['name']} left "
                            f"(you asked for {line['quantity']}).")
        else:
            price_moved |= row["price_kobo"] != line["unit_price_kobo"]
            items.append({"item": row, "quantity": line["quantity"]})
    if problems:
        session.clear_quote()
        return ("Sorry, I couldn't reserve that: " + " ".join(problems) +
                " Tell me how you'd like to adjust it.")
    if price_moved:
        fresh = build_quote(branch, items)
        session.set_quote(fresh)
        return "Prices changed since I quoted you, so please check again:\n\n" + quote_text(branch, fresh)

    try:
        order = db.create_order_db(q["id"], q["branch_id"], session.phone, items,
                                   db.compute_totals(items))
    except ValueError as e:                      # lost a stock race at the last moment
        session.clear_quote()
        return f"Sorry, I couldn't reserve that: {e}. Tell me how you'd like to adjust it."
    session.record_order(q["id"])                # also clears the pending quote
    return receipt_text(branch, order)
