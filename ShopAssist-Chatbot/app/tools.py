"""LLM-callable tools. Every tool: validates input, hits the DB, returns a
short factual string. Prices/math NEVER computed by the LLM.

Context (phone/session) travels via contextvars set per request in agent.py,
so tools stay pure and testable.
"""
import contextvars
import hashlib

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import db
from app.config import CURRENCY, HUMAN_ESCALATION_WEBHOOK
from app.session import get_session

_phone_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("phone")


def set_context(phone: str):
    _phone_ctx.set(phone)


def _phone() -> str:
    return _phone_ctx.get("")


def _money(x: float) -> str:
    return f"{CURRENCY}{x:,.2f}"


class CartItem(BaseModel):
    """One line item in a quote or order."""
    product_id: str = Field(description="Product ID, e.g. P-1001")
    quantity: int = Field(ge=1, le=99, description="How many units")


def _resolve(items: list[CartItem]) -> tuple[list[dict], list[str]]:
    """Look up products + validate stock. Returns (resolved, errors)."""
    resolved, errors = [], []
    for it in items:
        p = db.get_product(it.product_id)
        if not p:
            errors.append(f"Unknown product ID '{it.product_id}'.")
        elif p["stock_qty"] < it.quantity:
            errors.append(
                f"{p['name']}: only {p['stock_qty']} in stock "
                f"(you asked for {it.quantity}).")
        else:
            resolved.append({"product": p, "quantity": it.quantity})
    return resolved, errors


@tool
def search_products(keyword: str) -> str:
    """Search the catalog by product name, category, or keyword.
    Returns product IDs, names, unit prices and stock levels."""
    rows = db.search_products(keyword)
    if not rows:
        return f"No products matched '{keyword}'. Try a broader term."
    return "\n".join(
        f"- {r['product_id']}: {r['name']} — {_money(r['unit_price'])} "
        f"({r['stock_qty']} in stock)" for r in rows)


@tool
def get_product_details(product_id: str) -> str:
    """Get full details (name, description, price, stock) for one product ID."""
    p = db.get_product(product_id)
    if not p:
        return f"No product with ID '{product_id}'."
    return (f"{p['product_id']}: {p['name']}\n"
            f"Description: {p['description']}\n"
            f"Price: {_money(p['unit_price'])}\n"
            f"Stock: {p['stock_qty']} units")


@tool
def check_inventory(product_id: str) -> str:
    """Check how many units of a product are currently available."""
    p = db.get_product(product_id)
    if not p:
        return f"No product with ID '{product_id}'."
    return (f"{p['name']}: {p['stock_qty']} units available."
            if p["stock_qty"] > 0 else
            f"{p['name']} is currently OUT OF STOCK.")


@tool
def get_price_quote(items: list[CartItem]) -> str:
    """Calculate the full price for a list of items: subtotal, tax, delivery
    fee and total. Always use this before discussing prices with a customer."""
    resolved, errors = _resolve(items)
    if errors:
        return "Could not quote: " + " ".join(errors)
    if not resolved:
        return "No valid items to quote."
    totals = db.compute_totals(resolved)
    lines = [f"{i['quantity']} x {i['product']['name']} — "
             f"{_money(i['product']['unit_price'] * i['quantity'])}"
             for i in resolved]
    return ("\n".join(lines)
            + f"\nSubtotal: {_money(totals['subtotal'])}"
            + f"\nTax: {_money(totals['tax'])}"
            + f"\nDelivery: {_money(totals['delivery'])}"
            + f"\n*TOTAL: {_money(totals['total'])}*")


@tool
def create_order(items: list[CartItem]) -> str:
    """PLACE AN ORDER for confirmed items. STRICT RULE: only call this after
    the customer has explicitly confirmed the exact items and total.
    Returns the order ID."""
    resolved, errors = _resolve(items)
    if errors:
        return "Order failed: " + " ".join(errors)
    phone = _phone()
    # idempotency key: same phone + same basket = same order, even on retry
    raw = "|".join(sorted(f"{i['product']['product_id']}:{i['quantity']}"
                          for i in resolved))
    key = hashlib.sha256(f"{phone}|{raw}".encode()).hexdigest()
    session = get_session(phone)
    if session.last_order_key == key:
        return "This order was already placed. Check with track_order."
    totals = db.compute_totals(resolved)
    try:
        order_id = db.create_order_db(key, phone, resolved, totals)
    except ValueError as e:
        return f"Order failed: {e}"
    session.last_order_key = key
    session.cart.clear()
    return (f"Order {order_id} placed successfully! "
            f"Total charged: {_money(totals['total'])}. "
            "You can track it anytime with track_order.")


@tool
def track_order(order_id: str = "") -> str:
    """Check the status of recent orders. Optionally filter by order ID;
    otherwise shows the customer's most recent orders."""
    phone = _phone()
    rows = (db.get_conn().execute(
                "SELECT * FROM orders WHERE order_id = ? AND customer_phone = ?",
                (order_id.upper(), phone)).fetchall()
            if order_id else db.orders_for_phone(phone))
    if not rows:
        return "No orders found for this number."
    return "\n".join(
        f"- {r['order_id']}: {r['status']}, total {_money(r['total'])}, "
        f"placed {r['created_at']}" for r in rows)


@tool
async def escalate_to_human(reason: str) -> str:
    """Hand the conversation to a human agent (refunds, complaints, anything
    outside the bot's abilities). Call this rather than guessing."""
    get_session(_phone()).handoff = True
    if HUMAN_ESCALATION_WEBHOOK:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(HUMAN_ESCALATION_WEBHOOK,
                              json={"phone": _phone(), "reason": reason})
    return ("I've passed you to a human agent — they'll join this chat "
            "shortly. Thanks for your patience!")


ALL_TOOLS = [search_products, get_product_details, check_inventory,
             get_price_quote, create_order, track_order, escalate_to_human]
