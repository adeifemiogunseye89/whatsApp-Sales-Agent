"""LLM-callable tools. Every tool: validates input, hits the DB, returns a
short factual string. Prices/math NEVER computed by the LLM.

Context (phone/session) travels via contextvars set per request in agent.py,
so tools stay pure and testable.

Stage 1: every shelf tool is scoped to the customer's CONFIRMED branch, which
lives in the Session (code-owned state), not in the model's chat memory.

Stage 3: the model can only PREPARE a quote (get_price_quote). It cannot place
an order; app/orders.py does that after the customer answers YES.
"""
import contextvars

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import db, orders
from app.config import HUMAN_ESCALATION_WEBHOOK
from app.session import get_session
from app.textfmt import money as _money, wat as _wat   # shared formatting

_phone_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("phone")

def set_context(phone: str):
    _phone_ctx.set(phone)


def _phone() -> str:
    return _phone_ctx.get("")


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
    if b["branch_id"] == session.branch_id:
        return (f"*{b['name']}* is already the confirmed pickup branch. Tell the "
                "customer so briefly, then carry on with what they asked.")
    session.propose_branch(b["branch_id"])
    return (f"Proposed branch: *{b['name']}* — {b['address']}. "
            f'Tell the customer exactly this, naming the branch and its address: '
            f'"Your pickup branch would be *{b["name"]}* — {b["address"]}. '
            f'Is that right? (yes/no)"')


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
            "now come from this branch. Briefly confirm it to the customer, then "
            "carry on with what they originally asked (look up that product, "
            "quote, etc.) - do not ask the location question again.")


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
    """Price a basket at the confirmed branch and PREPARE it for the customer.
    Call this whenever the customer wants specific items/quantities priced or
    says they want to buy or order. The SYSTEM then shows the customer an
    itemised quote and places the order itself when they reply YES."""
    branch, err = _branch()
    if err:
        return err
    merged: dict[str, int] = {}                      # same product listed twice -> one line
    for it in items:
        pid = it.product_id.strip().upper()
        merged[pid] = merged.get(pid, 0) + it.quantity
    if any(q > 99 for q in merged.values()):
        return "Could not quote: the maximum is 99 of any one item."
    items = [CartItem(product_id=p, quantity=q) for p, q in merged.items()]
    resolved, errors = _resolve(branch["branch_id"], items)
    if errors:
        return "Could not quote: " + " ".join(errors)
    if not resolved:
        return "No valid items to quote."
    session = get_session(_phone())
    quote = orders.build_quote(branch, resolved)
    session.set_quote(quote)                         # saved: survives a restart
    session.set_outbox(orders.quote_text(branch, quote))   # shown to the customer verbatim
    return ("QUOTE PREPARED. The system is showing the customer the itemised quote "
            "and will place the order itself when they reply YES. Do not repeat the "
            "quote and do not say an order was placed.")


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


# NOTE (Stage 3): there is deliberately NO create_order tool. Orders are placed
# by code (orders.confirm_pending) after the customer replies YES to a quote.
ALL_TOOLS = [set_pickup_location, confirm_pickup_branch, search_products,
             get_product_details, check_inventory, get_price_quote,
             track_order, escalate_to_human]
