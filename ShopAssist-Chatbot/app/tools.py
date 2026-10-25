"""LLM-callable tools. Every tool: validates input, hits the DB, returns a
short factual string. Prices/math NEVER computed by the LLM.

Context (phone/session) travels via contextvars set per request in agent.py,
so tools stay pure and testable.

Stage 1: every shelf tool is scoped to the customer's CONFIRMED branch, which
lives in the Session (code-owned state), not in the model's chat memory.
"""
import contextvars
import hashlib
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import db
from app.config import CURRENCY, HUMAN_ESCALATION_WEBHOOK
from app.session import get_session

_phone_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("phone")

_WAT = timezone(timedelta(hours=1))   # Nigeria: UTC+1, no daylight saving


def set_context(phone: str):
    _phone_ctx.set(phone)


def _phone() -> str:
    return _phone_ctx.get("")


def _money(kobo: int) -> str:
    """12345600 -> '₦123,456'   |   1050 -> '₦10.50'   (integer math only)"""
    naira, rem = divmod(kobo, 100)
    return f"{CURRENCY}{naira:,}" + (f".{rem:02d}" if rem else "")


def _wat(utc_text: str) -> str:
    """DB stores UTC text; customers see Lagos time."""
    dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(_WAT).strftime("%d %b %Y, %I:%M %p")


NO_BRANCH_MSG = ("NO_BRANCH: no pickup branch is confirmed yet. Ask the customer "
                 "which city or area, then call set_pickup_location.")


def _branch():
    """-> (branch_row, None) if a branch is confirmed, else (None, error_text)."""
    bid = get_session(_phone()).branch_id
    branch = db.get_branch(bid) if bid else None
    if not branch or not branch["active"]:
        return None, NO_BRANCH_MSG
    return branch, None


class CartItem(BaseModel):
    """One line item in a quote or order."""
    product_id: str = Field(description="Product ID, e.g. P-1001")
    quantity: int = Field(ge=1, le=99, description="How many units")


def _resolve(branch_id: str, items: list[CartItem]) -> tuple[list[dict], list[str]]:
    """Look up products AT THIS BRANCH + validate stock. Returns (resolved, errors)."""
    resolved, errors = [], []
    for it in items:
        row = db.get_item(branch_id, it.product_id)
        if not row:
            errors.append(f"'{it.product_id}' is not stocked at this branch.")
        elif row["stock_qty"] < it.quantity:
            errors.append(f"{row['name']}: only {row['stock_qty']} in stock "
                          f"(you asked for {it.quantity}).")
        else:
            resolved.append({"item": row, "quantity": it.quantity})
    return resolved, errors


# ---------- branch selection (writes the session lock) ----------

@tool
def set_pickup_location(place: str) -> str:
    """Find the pickup branch for a city, area or branch name (e.g. 'Abuja',
    'Lekki', 'Ikeja Branch'). Also use this when the customer asks to switch
    branch. If exactly one branch matches it is PROPOSED and the customer must
    confirm; if several match, ask which one they want."""
    matches = db.find_branches(place)
    session = get_session(_phone())
    session.propose_branch(None)
    if not matches:
        return (f"No branch found for '{place}'. We currently serve: "
                f"{', '.join(db.served_cities())}.")
    if len(matches) > 1:
        lines = "\n".join(f"- {b['name']} — {b['address']}" for b in matches)
        return ("Several branches match. Ask the customer which one, then call "
                "set_pickup_location again with that branch name:\n" + lines)
    b = matches[0]
    session.propose_branch(b["branch_id"])
    return (f"Proposed branch: *{b['name']}* — {b['address']}. "
            "Ask the customer to confirm (yes/no).")

@tool
def confirm_pickup_branch(accepted: bool) -> str:
    """Call with accepted=true when the customer says yes to the proposed
    branch, accepted=false when they say no."""
    session = get_session(_phone())
    proposed = db.get_branch(session.proposed_branch_id) if session.proposed_branch_id else None
    if not proposed:
        return "No branch is waiting for confirmation. Call set_pickup_location first."
    if not accepted:
        session.propose_branch(None)
        return "Okay, not that one. Ask for another city, area or branch."
    session.lock_branch(proposed["branch_id"])
    return (f"Pickup branch locked: *{proposed['name']}*. All prices and stock "
            "now come from this branch.")

# ---------- shelf tools (all branch-scoped) ----------

@tool
def search_products(keyword: str) -> str:
    """Search the confirmed branch's catalog by product name, category or
    keyword. Returns product IDs, names, unit prices and stock levels."""
    branch, err = _branch()
    if err:
        return err
    rows = db.search_products(branch["branch_id"], keyword)
    if not rows:
        return f"No products matched '{keyword}' at {branch['name']}. Try a broader term."
    return f"At {branch['name']}:\n" + "\n".join(
        f"- {r['product_id']}: {r['name']} — {_money(r['price_kobo'])} "
        f"({r['stock_qty']} in stock)" for r in rows)


@tool
def get_product_details(product_id: str) -> str:
    """Get full details (name, description, price, stock) for one product ID
    at the confirmed branch."""
    branch, err = _branch()
    if err:
        return err
    p = db.get_item(branch["branch_id"], product_id)
    if not p:
        return f"'{product_id}' is not stocked at {branch['name']}."
    return (f"{p['product_id']}: {p['name']} ({branch['name']})\n"
            f"Description: {p['description']}\n"
            f"Price: {_money(p['price_kobo'])}\n"
            f"Stock: {p['stock_qty']} units")


@tool
def check_inventory(product_id: str) -> str:
    """Check how many units of a product are available at the confirmed branch."""
    branch, err = _branch()
    if err:
        return err
    p = db.get_item(branch["branch_id"], product_id)
    if not p:
        return f"'{product_id}' is not stocked at {branch['name']}."
    return (f"{p['name']} at {branch['name']}: {p['stock_qty']} units available."
            if p["stock_qty"] > 0 else
            f"{p['name']} is OUT OF STOCK at {branch['name']}.")


@tool
def get_price_quote(items: list[CartItem]) -> str:
    """Calculate the full price for a list of items at the confirmed branch:
    subtotal, any tax/fees, and total. Always use this before discussing
    prices with a customer."""
    branch, err = _branch()
    if err:
        return err
    resolved, errors = _resolve(branch["branch_id"], items)
    if errors:
        return "Could not quote: " + " ".join(errors)
    if not resolved:
        return "No valid items to quote."
    t = db.compute_totals(resolved)
    lines = [f"{i['quantity']} x {i['item']['name']} — "
             f"{_money(i['item']['price_kobo'] * i['quantity'])}" for i in resolved]
    if t["tax"]:
        lines.append(f"Tax: {_money(t['tax'])}")
    if t["delivery"]:
        lines.append(f"Fee: {_money(t['delivery'])}")
    return (f"Quote for {branch['name']}:\n" + "\n".join(lines)
            + f"\n*TOTAL (pay at pickup): {_money(t['total'])}*")


@tool
def create_order(items: list[CartItem]) -> str:
    """PLACE AN ORDER (reserve for pickup) at the confirmed branch. STRICT RULE:
    only call this after the customer has explicitly confirmed the exact items
    and total. Returns the order ID and pickup code."""
    branch, err = _branch()
    if err:
        return err
    resolved, errors = _resolve(branch["branch_id"], items)
    if errors:
        return "Order failed: " + " ".join(errors)
    phone = _phone()
    # NOTE (known issue, fixed in Stage 3): key = phone+branch+basket means the
    # same basket re-ordered later is treated as a retry. Needs a quote id.
    raw = "|".join(sorted(f"{i['item']['product_id']}:{i['quantity']}" for i in resolved))
    key = hashlib.sha256(f"{phone}|{branch['branch_id']}|{raw}".encode()).hexdigest()
    session = get_session(phone)
    if session.last_order_key == key:
        return "This order was already placed. Check with track_order."
    totals = db.compute_totals(resolved)
    try:
        order = db.create_order_db(key, branch["branch_id"], phone, resolved, totals)
    except ValueError as e:
        return f"Order failed: {e}"
    session.record_order(key)
    return (f"Order {order['order_id']} reserved at {branch['name']}. "
            f"Pickup code: {order['pickup_code']}. "
            f"Total to pay at pickup: {_money(order['total_kobo'])}. "
            f"Held until {_wat(order['expires_at'])}. "
            "Track it anytime with track_order.")

@tool
def track_order(order_id: str = "") -> str:
    """Check the status of recent orders. Optionally filter by order ID;
    otherwise shows the customer's most recent orders."""
    phone = _phone()
    if order_id:
        one = db.get_order_for_phone(order_id, phone)
        rows = [one] if one else []
    else:
        rows = db.orders_for_phone(phone)
    if not rows:
        return "No orders found for this number."
    return "\n".join(
        f"- {r['order_id']}: {r['status']} at {r['branch_name']}, "
        f"total {_money(r['total_kobo'])}, pickup code {r['pickup_code']}, "
        f"placed {_wat(r['created_at'])}" for r in rows)

@tool
def escalate_to_human(reason: str) -> str:
    """Hand the conversation to a human agent (refunds, complaints, anything
    outside the bot's abilities). Call this rather than guessing."""
    phone = _phone()
    session = get_session(phone)
    payload = {
        "phone": phone, "reason": reason, "branch_id": session.branch_id,
        "recent_messages": [{"role": m.type, "content": m.content}
                            for m in session.messages[-6:]
                            if isinstance(getattr(m, "content", None), str)],
    }
    if HUMAN_ESCALATION_WEBHOOK:
        try:
            httpx.post(HUMAN_ESCALATION_WEBHOOK, json=payload, timeout=10).raise_for_status()
        except Exception as e:
            # Do NOT silence the bot if no human was notified: the customer
            # would be left talking to nobody.
            print(f"[escalation failed] {phone}: {e!r}")
            return ("ESCALATION FAILED: the team could not be reached right now. "
                    "Apologise, ask the customer to try again in a few minutes.")
    else:
        print(f"[escalation - no webhook configured (dev)] {payload}")
    session.start_handoff()
    return ("I've passed you to a human agent — they'll join this chat "
            "shortly. Thanks for your patience!")
            
ALL_TOOLS = [set_pickup_location, confirm_pickup_branch, search_products,
             get_product_details, check_inventory, get_price_quote,
             create_order, track_order, escalate_to_human]
