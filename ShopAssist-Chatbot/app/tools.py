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
import json

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import branches, db, orders
from app.config import HUMAN_ESCALATION_WEBHOOK
from app.session import get_session
from app.textfmt import money as _money, wat as _wat   # shared formatting

_phone_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("phone")

def set_context(phone: str):
    _phone_ctx.set(phone)


def _phone() -> str:
    return _phone_ctx.get("")


BRANCH_SHOWN = ("BRANCH MESSAGE SHOWN. The system has already shown the customer the branch "
                "information and will handle their reply. Do not repeat it and do not write "
                "branch names or addresses.")

QUOTA_SHOWN = ("RESERVATION LIMIT REACHED. The system has told the customer when they can reserve "
               "again. Add nothing about reservations.")

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


def _resolve(branch_id: str, items: list[CartItem],
             own: dict[str, int] | None = None) -> tuple[list[dict], list[str]]:
    """Look up products AT THIS BRANCH + validate stock. Returns (resolved, errors).
    own: units the customer already holds in the reservation being changed (they count as available)."""
    own = own or {}
    resolved, errors = [], []
    for it in items:
        row = db.get_item(branch_id, it.product_id)
        available = (row["stock_qty"] + own.get(row["product_id"], 0)) if row else 0
        if not row:
            errors.append(f"'{it.product_id}' is not stocked at this branch.")
        elif available < it.quantity:
            errors.append(f"{row['name']}: only {available} in stock "
                          f"(you asked for {it.quantity}).")
        else:
            resolved.append({"item": row, "quantity": it.quantity})
    return resolved, errors


# ---------- branch selection (writes the session lock) ----------

@tool
def set_pickup_location(place: str) -> str:
    """Find pickup branches for a city, area or branch name (e.g. 'Abuja',
    'Lekki', 'Ikeja Branch'). Call this whenever the customer names a place,
    asks which branches exist, or wants to switch branch. The SYSTEM shows the
    customer the result and handles their yes/no itself."""
    matches = db.find_branches(place)
    session = get_session(_phone())
    session.propose_branch(None)
    if not matches:
        session.set_outbox(branches.none_text(place))
        return BRANCH_SHOWN
    if len(matches) > 1:
        session.set_outbox(branches.list_text(matches))
        return BRANCH_SHOWN
    b = matches[0]
    if b["branch_id"] == session.branch_id:
        return (f"*{b['name']}* is already the confirmed pickup branch. Tell the "
                "customer so briefly, then carry on with what they asked.")
    session.propose_branch(b["branch_id"])
    session.set_outbox(branches.proposal_text(b))     # shown verbatim; YES/NO handled by code
    return BRANCH_SHOWN


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
    says they want to buy or order a NEW reservation. The SYSTEM then shows the
    customer an itemised quote and places the order itself when they reply YES."""
    branch, err = _branch()
    if err:
        return err
    phone = _phone()
    session = get_session(phone)
    _, opens_at = db.reservation_window(phone)
    if opens_at:                                     # 3 per phone per 24 hours
        session.set_outbox(orders.quota_text(opens_at))
        return QUOTA_SHOWN
    items = _merge_items(items)
    if isinstance(items, str):
        return items
    resolved, errors = _resolve(branch["branch_id"], items)
    if errors:
        return "Could not quote: " + " ".join(errors)
    if not resolved:
        return "No valid items to quote."
    quote = orders.build_quote(branch, resolved)
    session.set_quote(quote)                         # saved: survives a restart
    session.set_outbox(orders.show_quote(session, branch, quote))   # shown verbatim
    return ("QUOTE PREPARED. The system is showing the customer the itemised quote "
            "and will place the order itself when they reply YES. Do not repeat the "
            "quote and do not say an order was placed.")


def _merge_items(items: list[CartItem]):
    """Same product listed twice -> one line. Returns the merged list, or an error string."""
    merged: dict[str, int] = {}
    for it in items:
        pid = it.product_id.strip().upper()
        merged[pid] = merged.get(pid, 0) + it.quantity
    if any(q > 99 for q in merged.values()):
        return "Could not quote: the maximum is 99 of any one item."
    return [CartItem(product_id=p, quantity=q) for p, q in merged.items()]


def _target_order(order_id: str, question: str):
    """Which OPEN reservation does the customer mean? -> (order, None) or (None, customer_text)."""
    open_orders = db.open_orders_for_phone(_phone())
    if not open_orders:
        return None, "You don't have any open reservations."
    if order_id:
        wanted = order_id.strip().upper()
        match = next((o for o in open_orders if o["order_id"] == wanted), None)
        if match:
            return match, None
        return None, orders.open_list_text(
            open_orders, f"I couldn't find an open reservation {wanted}. Your open reservations:")
    if len(open_orders) == 1:
        return open_orders[0], None
    return None, orders.open_list_text(open_orders, question)


@tool
def cancel_order(order_id: str = "") -> str:
    """Prepare to CANCEL one of the customer's open reservations. Leave order_id empty
    if they have only one. The SYSTEM shows the customer what will be cancelled and
    cancels only after they reply YES."""
    session = get_session(_phone())
    order, text = _target_order(order_id, "Which reservation would you like to cancel?")
    session.set_outbox(text if order is None else orders.prepare_cancel(session, order))
    return orders.ACTION_SHOWN


@tool
def amend_order(items: list[CartItem], order_id: str = "") -> str:
    """Prepare to CHANGE the items of one of the customer's open reservations ("make it
    3 instead of 8"). Pass the FULL new basket, not just the difference. The SYSTEM shows
    the old and new versions and changes the reservation only after they reply YES.
    The pickup code and hold time stay the same and no extra reservation is used."""
    session = get_session(_phone())
    order, text = _target_order(order_id, "Which reservation would you like to change?")
    if order is None:
        session.set_outbox(text)
        return orders.ACTION_SHOWN
    items = _merge_items(items)
    if isinstance(items, str):
        return items.replace("quote", "change")
    own = {i["product_id"]: i["quantity"] for i in json.loads(order["items_json"])}
    resolved, errors = _resolve(order["branch_id"], items, own)
    if errors:
        return "Could not change it: " + " ".join(errors) + " The reservation is unchanged."
    if not resolved:
        return "No valid items to change to."
    if sorted((r["item"]["product_id"], r["quantity"]) for r in resolved) == sorted(own.items()):
        return "The reservation already contains exactly that. Tell the customer nothing needs changing."
    session.set_outbox(orders.prepare_amend(session, order, resolved))
    return orders.ACTION_SHOWN


def _status_text(r) -> str:
    if r["status"] != "RESERVED":
        return r["status"]
    if r["past_release"]:
        return "EXPIRED"
    if r["in_grace"]:
        return f"RESERVED (grace period until {_wat(r['release_at'])})"
    return f"RESERVED (held until {_wat(r['expires_at'])})"


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
        f"- {r['order_id']}: {_status_text(r)} at {r['branch_name']}, "
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


# NOTE: there is deliberately NO create_order and NO confirm_pickup_branch tool.
# Orders (orders.confirm_pending) and branches (branches.confirm_proposal) are
# confirmed by CODE after the customer replies YES.
ALL_TOOLS = [set_pickup_location, search_products,
             get_product_details, check_inventory, get_price_quote,
             amend_order, cancel_order, track_order, escalate_to_human]
