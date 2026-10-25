"""Stage 1 verification: branch-aware data model + branch-scoped tools.

Run from the project root:   python tests/test_stage1.py
(also works with `pytest tests/test_stage1.py`). Needs NO Ollama and NO WhatsApp:
it exercises the database and the tool functions directly.

Each test is labelled with the Stage-1 NEED it proves (N1..N5).
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Must be set BEFORE importing app.* (config reads env at import time).
_TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_shop.db")

from app import db, orders, tools              # noqa: E402
from app.session import clear, get_session     # noqa: E402

db.init_db()
LEKKI, IKEJA, WUSE = "BR-LKI", "BR-IKJ", "BR-WUS"


def stock(branch, pid):
    return db.get_item(branch, pid)["stock_qty"]


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def lock_branch(phone, place):
    call(tools.set_pickup_location, phone, place=place)
    call(tools.confirm_pickup_branch, phone, accepted=True)


# ---- N1: prices and stock are per branch; no cross-branch visibility ----
def test_n1_branch_isolation():
    lk = db.get_item(LEKKI, "P-1001")["price_kobo"]
    wu = db.get_item(WUSE, "P-1001")["price_kobo"]
    assert lk != wu, "same product should carry different prices per branch"
    assert db.get_item(IKEJA, "P-1003") is None, "Ikeja doesn't stock jasmine rice"
    assert all(r["product_id"] != "P-1003" for r in db.search_products(IKEJA, "rice"))
    assert db.get_item(WUSE, "P-1003")["stock_qty"] == 0   # stocked but sold out


def test_n1_search_matches_words_in_any_order():
    rows = db.search_products(LEKKI, "rice 5kg basmati")
    assert [r["product_id"] for r in rows] == ["P-1001"]


# ---- N2: integer kobo money; no fee on pickup orders; tax configurable ----
def test_n2_money_is_integer_and_pickup_has_no_fee():
    row = db.get_item(LEKKI, "P-1002")
    assert isinstance(row["price_kobo"], int)
    t = db.compute_totals([{"item": row, "quantity": 2}])
    assert all(isinstance(v, int) for v in t.values())
    assert t["delivery"] == 0 and t["tax"] == 0 and t["total"] == 960000


def test_n2_tax_when_enabled_stays_integer():
    old = db.TAX_RATE
    db.TAX_RATE = 0.075
    try:
        t = db.compute_totals([{"item": db.get_item(LEKKI, "P-1002"), "quantity": 1}])
        assert t["tax"] == 36000 and isinstance(t["tax"], int)
    finally:
        db.TAX_RATE = old


def test_n2_money_formatting():
    assert tools._money(1450000) == "₦14,500"
    assert tools._money(1050) == "₦10.50"


# ---- N3: order carries branch/code/expiry; stock moves only at that branch ----
def test_n3_order_is_branch_scoped_and_atomic():
    before = {b: stock(b, "P-2001") for b in (LEKKI, IKEJA, WUSE)}
    item = db.get_item(LEKKI, "P-2001")
    items = [{"item": item, "quantity": 3}]
    o = db.create_order_db("k-n3", LEKKI, "2348000000001", items,
                           db.compute_totals(items))
    assert o["branch_id"] == LEKKI and o["status"] == "RESERVED"
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", o["pickup_code"])
    assert o["expires_at"] > o["created_at"]
    assert stock(LEKKI, "P-2001") == before[LEKKI] - 3
    assert stock(IKEJA, "P-2001") == before[IKEJA]      # untouched
    assert stock(WUSE, "P-2001") == before[WUSE]        # untouched


def test_n3_same_key_is_a_retry_not_a_second_order():
    item = db.get_item(LEKKI, "P-4001")
    items = [{"item": item, "quantity": 1}]
    s0 = stock(LEKKI, "P-4001")
    a = db.create_order_db("k-retry", LEKKI, "2348000000002", items, db.compute_totals(items))
    b = db.create_order_db("k-retry", LEKKI, "2348000000002", items, db.compute_totals(items))
    assert a["order_id"] == b["order_id"] and stock(LEKKI, "P-4001") == s0 - 1


def test_n3_oversell_rolls_back_everything():
    ok_item = db.get_item(WUSE, "P-2001")
    short_item = db.get_item(WUSE, "P-1001")            # only 6 at Wuse
    items = [{"item": ok_item, "quantity": 2}, {"item": short_item, "quantity": 99}]
    s_ok, s_short = stock(WUSE, "P-2001"), stock(WUSE, "P-1001")
    try:
        db.create_order_db("k-oversell", WUSE, "2348000000003", items,
                           db.compute_totals(items))
        raise AssertionError("should have refused")
    except ValueError:
        pass
    assert stock(WUSE, "P-2001") == s_ok, "first line's stock must be restored"
    assert stock(WUSE, "P-1001") == s_short
    assert db.find_order_by_key("k-oversell") is None


# ---- N4: branch is code-owned state; tools refuse to run without it ----
def test_n4_tools_refuse_without_branch():
    p = "2348000000010"; clear(p)
    cart = [tools.CartItem(product_id="P-1001", quantity=1)]
    for fn, args in [(tools.search_products, {"keyword": "rice"}),
                     (tools.check_inventory, {"product_id": "P-1001"}),
                     (tools.get_price_quote, {"items": cart})]:   # create_order no longer a tool (Stage 3)
        assert call(fn, p, **args).startswith("NO_BRANCH")


def test_n4_propose_confirm_flow_and_lock_survives_history_loss():
    p = "2348000000011"; clear(p)
    out = call(tools.set_pickup_location, p, place="Lagos")
    assert "Several branches" in out and "Lekki" in out and "Ikeja" in out
    assert "Proposed branch" in call(tools.set_pickup_location, p, place="Lekki")
    assert "Okay, not that one" in call(tools.confirm_pickup_branch, p, accepted=False)
    assert get_session(p).branch_id is None, "a 'no' must not lock anything"
    call(tools.set_pickup_location, p, place="Ikeja Branch")
    assert "locked" in call(tools.confirm_pickup_branch, p, accepted=True)
    get_session(p).messages.clear()                     # simulate history truncation
    assert "Ikeja Branch" in call(tools.search_products, p, keyword="rice")


def test_n4_unknown_place_lists_served_cities():
    out = call(tools.set_pickup_location, "2348000000012", place="Kano")
    assert "No branch found" in out and "Abuja" in out and "Lagos" in out


def test_n4_switching_branch_changes_prices():
    p = "2348000000013"; clear(p)
    lock_branch(p, "Lekki")
    lekki_out = call(tools.search_products, p, keyword="basmati")
    lock_branch(p, "Wuse")
    wuse_out = call(tools.search_products, p, keyword="basmati")
    assert "₦14,500" in lekki_out and "₦15,200" in wuse_out


# ---- N3+N4 end-to-end through the tools the LLM will call ----
def test_end_to_end_order_and_tracking():
    # Stage 3: the model PREPARES a quote; code shows it and places the order.
    p = "2348000000014"; clear(p)
    lock_branch(p, "Abuja")
    cart = [tools.CartItem(product_id="P-1002", quantity=2)]
    assert "QUOTE PREPARED" in call(tools.get_price_quote, p, items=cart)
    shown = get_session(p).outbox                      # what the customer is shown
    assert "Wuse Branch" in shown and "₦10,000" in shown and "pay at pickup" in shown
    assert "Fee" not in shown and "Tax" not in shown
    placed = orders.confirm_pending(get_session(p))
    m = re.search(r"Order ID: \*(ORD-[A-Z0-9]+)\*\nPickup code: \*([A-Z0-9]{6})\*", placed)
    assert m and "Order reserved at Wuse Branch" in placed, placed
    tracked = call(tools.track_order, p)
    assert m.group(1) in tracked and m.group(2) in tracked and "RESERVED" in tracked
    # another customer must not see it
    assert "No orders found" in call(tools.track_order, "2348099999999", order_id=m.group(1))


def test_out_of_stock_and_not_stocked_messages():
    p = "2348000000015"; clear(p)
    lock_branch(p, "Wuse")
    assert "OUT OF STOCK" in call(tools.check_inventory, p, product_id="P-1003")
    lock_branch(p, "Ikeja")
    assert "not stocked" in call(tools.check_inventory, p, product_id="P-1003")


# ---- N5: the prompt only references tools that really exist ----
def test_n5_every_tool_named_in_prompt_exists():
    try:
        from app.agent import SYSTEM_PROMPT
    except ImportError:
        print("  (skipped: langgraph/langchain-ollama not installed)")
        return
    named = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", SYSTEM_PROMPT))
    real = {t.name for t in tools.ALL_TOOLS}
    assert named <= real, f"prompt mentions tools that don't exist: {named - real}"
    assert {"set_pickup_location", "confirm_pickup_branch"} <= named


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"PASS  {name}")
        except Exception as e:
            failed += 1; print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
