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
from app.config import (DAILY_RESERVATION_LIMIT, GRACE_MINUTES, QUOTE_TTL_MINUTES,
                        RESERVATION_HOURS)
from app.textfmt import money, wat


# ---------- reading the customer's answer ----------

_YES = {"yes", "yeah", "yep", "yup", "yea", "ok", "okay", "okey", "sure", "confirm",
        "confirmed", "proceed", "correct", "ehen", "oya", "place", "ahead"}
_NO = {"no", "nope", "nah", "cancel", "stop", "never", "abort", "dont", "not"}
_FILLER = {"please", "pls", "plz", "kindly", "go", "it", "the", "this", "that", "order",
           "my", "now", "thanks", "thank", "you", "do", "i", "want", "to", "is", "right",
           "and", "abeg", "sharp", "quote"}


def parse_confirmation(text: str, cancel_means_yes: bool = False) -> str | None:
    """'yes' / 'no' ONLY when the whole message is a plain answer; anything
    else (numbers, questions, 'yes but make it 3') returns None and goes to the model.
    cancel_means_yes: when the open question is "Cancel this reservation?", the word
    "cancel" is a YES ("cancel it"), not a NO."""
    if re.search(r"[\d?]", text):
        return None
    yes_words = _YES | ({"cancel"} if cancel_means_yes else set())
    no_words = _NO - ({"cancel"} if cancel_means_yes else set())
    words = set(re.findall(r"[a-z]+", text.lower().replace("'", "")))
    if not words or not words <= (yes_words | no_words | _FILLER):
        return None
    yes, no = words & yes_words, words & no_words
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


def quote_text(branch, quote: dict, left: int | None = None, open_note: str | None = None) -> str:
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
              f"Pickup: {branch['address']}"]
    if open_note:
        lines.append(open_note)
    lines += ["", f"Reply *YES* to reserve this for pickup (held {RESERVATION_HOURS} hours) "
                  f"or *NO* to cancel. This quote is valid for {QUOTE_TTL_MINUTES} minutes."]
    if left is not None:
        lines.append(f"This uses 1 of your {DAILY_RESERVATION_LIMIT} reservations per 24 hours "
                     f"({left} left after this).")
    return "\n".join(lines)


def show_quote(session, branch, quote: dict) -> str:
    """quote_text plus the customer's own context: slots left, and any open reservation."""
    n, _ = db.reservation_window(session.phone)
    left = max(0, DAILY_RESERVATION_LIMIT - n - 1)
    open_orders = db.open_orders_for_phone(session.phone)
    note = (f"You already have an open reservation ({open_orders[0]['order_id']}); this would be "
            "a separate one. To change that one instead, say so.") if open_orders else None
    return quote_text(branch, quote, left, note)


def quota_text(opens_at_utc: str) -> str:
    return (f"You've reached the limit of {DAILY_RESERVATION_LIMIT} reservations in 24 hours "
            "(cancelled and expired ones count too). You can reserve again from "
            f"{wat(opens_at_utc)}. You can still ask me about prices and stock.")


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
        return "Prices changed since I quoted you, so please check again:\n\n" + show_quote(session, branch, fresh)

    try:
        order = db.create_order_db(q["id"], q["branch_id"], session.phone, items,
                                   db.compute_totals(items))
    except db.QuotaExceeded as e:                # the daily limit was reached meanwhile
        session.clear_quote()
        return quota_text(e.opens_at)
    except ValueError as e:                      # lost a stock race at the last moment
        session.clear_quote()
        return f"Sorry, I couldn't reserve that: {e}. Tell me how you'd like to adjust it."
    session.record_order(q["id"])                # also clears the pending quote
    return receipt_text(branch, order)


# ---------- cancelling and changing an open reservation (Stage 4) ----------
# The model only PREPARES these (tools.cancel_order / tools.amend_order). The text the
# customer reads, the YES/NO, the stock changes and the confirmation are all code.

ACTION_SHOWN = ("SHOWN TO CUSTOMER. The system has shown the customer the request and will "
                "act only after they reply YES. Add nothing.")


def _lines(items: list[dict]) -> list[str]:
    return [f"{i['quantity']} × {i['name']} @ {money(i['unit_price_kobo'])} = "
            f"{money(i['unit_price_kobo'] * i['quantity'])}" for i in items]


def open_list_text(open_orders, question: str) -> str:
    lines = [question]
    for n, o in enumerate(open_orders, 1):
        items = ", ".join(f"{i['quantity']} × {i['name']}" for i in json.loads(o["items_json"]))
        lines.append(f"{n}. *{o['order_id']}* — {items}; {money(o['total_kobo'])} at {o['branch_name']}")
    lines.append("Reply with the order ID.")
    return "\n".join(lines)


def _expiry() -> float:
    return _session._now() + QUOTE_TTL_MINUTES * 60


def prepare_cancel(session, order) -> str:
    session.set_action({"type": "cancel", "order_id": order["order_id"], "expires_at": _expiry()})
    items = json.loads(order["items_json"])
    return "\n".join([
        f"*Cancel reservation {order['order_id']} at {order['branch_name']}?*",
        *_lines(items),
        f"Total {money(order['total_kobo'])}", "",
        "Reply *YES* to cancel it or *NO* to keep it. A cancelled reservation still counts "
        f"toward your {DAILY_RESERVATION_LIMIT} per 24 hours."])


def prepare_amend(session, order, resolved: list[dict]) -> str:
    totals = db.compute_totals(resolved)
    new_items = [{"product_id": r["item"]["product_id"], "name": r["item"]["name"],
                  "quantity": r["quantity"], "unit_price_kobo": r["item"]["price_kobo"]}
                 for r in resolved]
    session.set_action({"type": "amend", "order_id": order["order_id"], "items": new_items,
                        "totals": totals, "expires_at": _expiry()})
    lines = [f"*Change reservation {order['order_id']} at {order['branch_name']}?*", "Now:"]
    lines += _lines(json.loads(order["items_json"]))
    lines += [f"Total {money(order['total_kobo'])}", "Change to:", *_lines(new_items)]
    if totals["tax"]:
        lines.append(f"Tax: {money(totals['tax'])}")
    if totals["delivery"]:
        lines.append(f"Fee: {money(totals['delivery'])}")
    lines += [f"*New total: {money(totals['total'])}* (pay at pickup)",
              "Same pickup code and hold time. This does not use another reservation.", "",
              "Reply *YES* to change it or *NO* to keep it as it is."]
    return "\n".join(lines)


def cancelled_text(order) -> str:
    return (f"*Reservation {order['order_id']} cancelled.*\nThe items are back on the shelf. "
            f"It still counts toward your {DAILY_RESERVATION_LIMIT} reservations per 24 hours.")


def amended_text(order) -> str:
    lines = [f"*Reservation {order['order_id']} updated*",
             f"Pickup code: *{order['pickup_code']}* (unchanged)"]
    lines += [f"{i['quantity']} × {i['name']}" for i in json.loads(order["items_json"])]
    lines += [f"Total to pay at pickup: *{money(order['total_kobo'])}*",
              f"Held until {wat(order['expires_at'])}. Show your pickup code at the counter."]
    return "\n".join(lines)


def decline_action(session) -> str:
    a = session.pending_action
    session.clear_action()
    return f"Okay, I've left reservation {a['order_id']} as it is." if a else "Okay."


def confirm_action(session) -> str:
    a = session.pending_action
    if not a:
        return "There's nothing waiting for confirmation."
    if session.action_expired():
        session.clear_action()
        return "That request expired, so nothing was changed. Tell me again if you still want it."
    try:
        if a["type"] == "cancel":
            order = db.cancel_order_db(a["order_id"], session.phone)
            session.clear_action()
            return cancelled_text(order)

        # amend: re-check price and stock now. The order's OWN units count as available.
        current = db.get_order_for_phone(a["order_id"], session.phone)
        if not current:
            session.clear_action()
            return "I couldn't find that reservation any more."
        own = {i["product_id"]: i["quantity"] for i in json.loads(current["items_json"])}
        items, problems, price_moved = [], [], False
        for line in a["items"]:
            row = db.get_item(current["branch_id"], line["product_id"])
            available = (row["stock_qty"] + own.get(line["product_id"], 0)) if row else 0
            if not row:
                problems.append(f"{line['name']} is no longer stocked here.")
            elif available < line["quantity"]:
                problems.append(f"only {available} of {line['name']} available to you "
                                f"(you asked for {line['quantity']}).")
            else:
                price_moved |= row["price_kobo"] != line["unit_price_kobo"]
                items.append({"item": row, "quantity": line["quantity"]})
        if problems:
            session.clear_action()
            return ("Sorry, I couldn't change it: " + " ".join(problems) +
                    " Your reservation is unchanged.")
        if price_moved:
            return ("Prices changed since I showed you, so please check again:\n\n"
                    + prepare_amend(session, current, items))
        order = db.amend_order_db(a["order_id"], session.phone, items, db.compute_totals(items))
        session.clear_action()
        return amended_text(order)
    except ValueError as e:
        session.clear_action()
        return f"Sorry, I couldn't do that: {e}. Your reservation is unchanged."
