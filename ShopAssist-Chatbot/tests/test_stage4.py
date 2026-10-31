"""Stage 4 verification: reservations that end, the 3-per-24-hours rule, cancel and amend.

Why (from live chats): old test reservations never expired, so basmati showed "1 in stock"
(12 on the shelf); "make it 3 instead of 8" on a PLACED order created a second order and
held 11 units. Run:  python tests/test_stage4.py   (no Ollama, no WhatsApp: the LLM is a fake)

NEEDS
  N1  hold + grace + release: stock returns exactly once when hold+grace ends
  N2  every customer turn (and the sweeper) releases expired holds
  N3  3 reservations per phone in a rolling 24h; cancelled/expired ones never give a slot back
  N4  cancel: the customer sees what is cancelled, YES cancels (code), stock returns
  N5  amend: change a placed reservation in place (same id, code, clock; no extra slot)
  N6  the model is told about open reservations; fake cancel/change claims are rejected
  N7  per-turn diagnostics record the path and corrections without storing customer text
  N8  an older database is upgraded in place
"""
import asyncio, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_shop4.db")
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")

from langchain_core.messages import AIMessage                      # noqa: E402

from app import branches, db, orders, tools                        # noqa: E402
from app import session as S                                       # noqa: E402
from app.config import GRACE_MINUTES, QUOTE_TTL_MINUTES, RESERVATION_HOURS  # noqa: E402

db.init_db()
LEKKI = "BR-LKI"
MILK, EGGS, JASMINE, LONG = "P-2001", "P-3001", "P-1003", "P-1002"


# ---------------- helpers ----------------
def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def restart():
    S._cache.clear()


class fake_time:
    def __init__(self, minutes): self.minutes = minutes
    def __enter__(self): self._o = S._now; S._now = lambda: self._o() + self.minutes * 60
    def __exit__(self, *a): S._now = self._o


class FakeAgent:
    def __init__(self, script=None, text="ok"):
        self.calls, self.script, self.text = [], script, text
    def invoke(self, inp):
        self.calls.append(list(inp["messages"]))
        out = self.script(inp) if self.script else self.text
        return {"messages": list(inp["messages"]) + [AIMessage(content=out)]}


def agent(fake):
    from app import agent as A
    A.agent = fake
    return A


def no_model():
    boom = FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("model must not run")))
    return agent(boom), boom


def shopper(phone):
    S.clear(phone)
    call(tools.set_pickup_location, phone, place="Lekki")
    branches.confirm_proposal(S.get_session(phone))
    S.get_session(phone).take_outbox()


def quote(phone, *lines):
    return call(tools.get_price_quote, phone,
                items=[tools.CartItem(product_id=p, quantity=q) for p, q in lines])


def reserve(phone, *lines):
    assert "QUOTE PREPARED" in quote(phone, *lines)
    assert "Order reserved" in orders.confirm_pending(S.get_session(phone))
    return db.orders_for_phone(phone, 1)[0]


def stock(pid, branch=LEKKI):
    return db.get_item(branch, pid)["stock_qty"]


def force(order_id, expires=None, release=None, created=None):
    """Move an order's clocks (SQL 'now' offsets, e.g. '-10 minutes')."""
    with db.get_conn() as c:
        if expires: c.execute("UPDATE orders SET expires_at = datetime('now', ?) WHERE order_id = ?", (expires, order_id))
        if release: c.execute("UPDATE orders SET release_at = datetime('now', ?) WHERE order_id = ?", (release, order_id))
        if created: c.execute("UPDATE orders SET created_at = datetime('now', ?) WHERE order_id = ?", (created, order_id))


def status(order_id):
    return db.get_conn().execute("SELECT status FROM orders WHERE order_id = ?", (order_id,)).fetchone()["status"]


def set_stock(pid, qty):
    with db.get_conn() as c:
        c.execute("UPDATE branch_inventory SET stock_qty = ? WHERE branch_id = ? AND product_id = ?", (qty, LEKKI, pid))


# ---------------- N1: hold, grace, release ----------------
def test_n1_order_has_hold_and_release_times():
    p = "2348600000001"; shopper(p); o = reserve(p, (MILK, 1))
    r = db.get_conn().execute(
        "SELECT datetime(created_at, ?) AS hold_end, datetime(expires_at, ?) AS rel, expires_at, release_at "
        "FROM orders WHERE order_id = ?", (f"+{RESERVATION_HOURS * 60} minutes", f"+{GRACE_MINUTES} minutes", o["order_id"])).fetchone()
    assert r["hold_end"] == r["expires_at"] and r["rel"] == r["release_at"]


def test_n1_release_returns_stock_exactly_once():
    p = "2348600000002"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 2)); assert stock(MILK) == s0 - 2
    force(o["order_id"], expires="-40 minutes", release="-1 minutes")
    assert db.release_expired() >= 1
    assert stock(MILK) == s0 and status(o["order_id"]) == "EXPIRED"
    assert db.get_conn().execute("SELECT closed_at FROM orders WHERE order_id = ?", (o["order_id"],)).fetchone()[0]
    db.release_expired(); db.release_expired()
    assert stock(MILK) == s0, "a second sweep must never return the units again"


def test_n1_nothing_released_during_hold_or_grace():
    p = "2348600000003"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 1))
    force(o["order_id"], expires="+1 hours", release="+90 minutes")
    db.release_expired()
    assert status(o["order_id"]) == "RESERVED" and not db.get_order_for_phone(o["order_id"], p)["in_grace"]
    force(o["order_id"], expires="-5 minutes", release="+25 minutes")
    db.release_expired()
    assert status(o["order_id"]) == "RESERVED" and db.get_order_for_phone(o["order_id"], p)["in_grace"]
    assert stock(MILK) == s0 - 1, "units stay held through the grace period"


def test_n1_track_order_shows_the_phase():
    p = "2348600000004"; shopper(p); o = reserve(p, (MILK, 1))
    assert "RESERVED (held until" in call(tools.track_order, p)
    force(o["order_id"], expires="-5 minutes", release="+25 minutes")
    assert "RESERVED (grace period until" in call(tools.track_order, p)
    force(o["order_id"], expires="-40 minutes", release="-1 minutes")
    assert ": EXPIRED at" in call(tools.track_order, p), "shown as expired even before the sweep runs"
    db.release_expired()
    assert ": EXPIRED at" in call(tools.track_order, p)


# ---------------- N2: sweeps ----------------
def test_n2_every_customer_turn_releases_expired_holds():
    p = "2348600000005"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 3)); force(o["order_id"], expires="-40 minutes", release="-1 minutes")
    A = agent(FakeAgent(text="hello")); A.run_agent("2348600000006", "hi")     # a DIFFERENT customer chats
    assert stock(MILK) == s0 and status(o["order_id"]) == "EXPIRED"


def test_n2_freed_stock_can_be_reserved_by_someone_else():
    a, b = "2348600000007", "2348600000008"; shopper(a); shopper(b)
    set_stock(JASMINE, 2)
    oa = reserve(a, (JASMINE, 2))
    assert "Could not quote" in quote(b, (JASMINE, 1)) and S.get_session(b).pending_quote is None
    force(oa["order_id"], expires="-40 minutes", release="-1 minutes"); db.release_expired()
    assert "QUOTE PREPARED" in quote(b, (JASMINE, 1))


def test_n2_the_server_sweeper_function_works():
    from app import main
    p = "2348600000009"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 1)); force(o["order_id"], expires="-40 minutes", release="-1 minutes")
    assert main.sweep_once() >= 1 and stock(MILK) == s0
    asyncio.run(main.startup())                       # startup (incl. creating the sweeper task) runs cleanly


# ---------------- N3: 3 per phone per rolling 24 hours ----------------
def test_n3_fourth_reservation_is_refused_with_the_time():
    p = "2348600000010"; shopper(p)
    for _ in range(3): reserve(p, (MILK, 1))
    out = quote(p, (MILK, 1))
    assert out.startswith("RESERVATION LIMIT REACHED")
    shown = S.get_session(p).take_outbox()
    assert "limit of 3 reservations in 24 hours" in shown and "can reserve again from" in shown
    assert S.get_session(p).pending_quote is None


def test_n3_cancelled_and_expired_never_give_a_slot_back():
    p = "2348600000011"; shopper(p)
    o1, o2, _ = (reserve(p, (MILK, 1)) for _ in range(3))
    db.cancel_order_db(o1["order_id"], p)
    force(o2["order_id"], expires="-40 minutes", release="-1 minutes"); db.release_expired()
    assert status(o1["order_id"]) == "CANCELLED" and status(o2["order_id"]) == "EXPIRED"
    assert quote(p, (MILK, 1)).startswith("RESERVATION LIMIT REACHED")


def test_n3_the_window_rolls_after_24_hours():
    p = "2348600000012"; shopper(p)
    first = reserve(p, (MILK, 1)); reserve(p, (MILK, 1)); reserve(p, (MILK, 1))
    force(first["order_id"], created="-23 hours")
    assert quote(p, (MILK, 1)).startswith("RESERVATION LIMIT REACHED")
    force(first["order_id"], created="-25 hours")
    assert "QUOTE PREPARED" in quote(p, (MILK, 1))


def test_n3_limit_is_rechecked_at_YES():
    p = "2348600000013"; shopper(p)
    reserve(p, (MILK, 1)); reserve(p, (MILK, 1))
    assert "QUOTE PREPARED" in quote(p, (MILK, 1))                  # 2 used: allowed to quote
    row = db.get_item(LEKKI, MILK)
    db.create_order_db("k-race-4-1", LEKKI, p, [{"item": row, "quantity": 1}], db.compute_totals([{"item": row, "quantity": 1}]))
    s0 = stock(MILK)
    out = orders.confirm_pending(S.get_session(p))
    assert "limit of 3 reservations" in out and stock(MILK) == s0 and len(db.orders_for_phone(p, 10)) == 3


def test_n3_limit_is_per_phone():
    a, b = "2348600000014", "2348600000015"; shopper(a); shopper(b)
    for _ in range(3): reserve(a, (MILK, 1))
    assert quote(a, (MILK, 1)).startswith("RESERVATION LIMIT")
    assert "QUOTE PREPARED" in quote(b, (MILK, 1))


def test_n3_quote_tells_the_customer_the_slots_left_and_open_reservation():
    p = "2348600000016"; shopper(p)
    quote(p, (MILK, 1))
    assert "This uses 1 of your 3 reservations per 24 hours (2 left after this)." in S.get_session(p).take_outbox()
    o = reserve(p, (MILK, 1))
    quote(p, (EGGS, 1))
    shown = S.get_session(p).take_outbox()
    assert "(1 left after this)" in shown and f"You already have an open reservation ({o['order_id']})" in shown


def test_n3_amending_uses_no_slot():
    p = "2348600000017"; shopper(p)
    for _ in range(3): last = reserve(p, (MILK, 2))
    call(tools.amend_order, p, items=[tools.CartItem(product_id=MILK, quantity=1)], order_id=last["order_id"])
    assert "updated" in orders.confirm_action(S.get_session(p))
    assert db.reservation_window(p)[0] == 3 and len(db.orders_for_phone(p, 10)) == 3


# ---------------- N4: cancel ----------------
def test_n4_cancel_needs_a_YES_then_code_releases_the_stock():
    p = "2348600000018"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 2))
    assert call(tools.cancel_order, p) == orders.ACTION_SHOWN
    shown = S.get_session(p).take_outbox()
    assert f"Cancel reservation {o['order_id']}" in shown and "2 × Whole Milk 1L" in shown
    assert S.get_session(p).pending_action["type"] == "cancel" and status(o["order_id"]) == "RESERVED"
    A, boom = no_model()
    out = A.run_agent(p, "yes")
    assert f"Reservation {o['order_id']} cancelled" in out and "still counts" in out and boom.calls == []
    assert status(o["order_id"]) == "CANCELLED" and stock(MILK) == s0
    assert ": CANCELLED at" in call(tools.track_order, p)


def test_n4_NO_keeps_the_reservation():
    p = "2348600000019"; shopper(p); o = reserve(p, (MILK, 1))
    call(tools.cancel_order, p); S.get_session(p).take_outbox()
    A, boom = no_model()
    assert "left reservation" in A.run_agent(p, "no") and status(o["order_id"]) == "RESERVED"
    assert S.get_session(p).pending_action is None


def test_n4_the_word_cancel_is_a_yes_inside_a_cancel_question():
    P = orders.parse_confirmation
    assert P("cancel", cancel_means_yes=True) == "yes" and P("cancel it", cancel_means_yes=True) == "yes"
    assert P("yes cancel it", cancel_means_yes=True) == "yes" and P("no", cancel_means_yes=True) == "no"
    assert P("dont cancel", cancel_means_yes=True) is None and P("no dont cancel it", cancel_means_yes=True) is None
    assert P("cancel") == "no", "outside a cancel question it still means NO (declines a quote)"


def test_n4_with_two_open_reservations_it_asks_which():
    p = "2348600000020"; shopper(p)
    o1 = reserve(p, (MILK, 1)); o2 = reserve(p, (EGGS, 1))
    call(tools.cancel_order, p)
    shown = S.get_session(p).take_outbox()
    assert o1["order_id"] in shown and o2["order_id"] in shown and "Reply with the order ID" in shown
    assert S.get_session(p).pending_action is None
    call(tools.cancel_order, p, order_id=o1["order_id"].lower())
    assert S.get_session(p).pending_action["order_id"] == o1["order_id"]


def test_n4_nobody_can_cancel_someone_elses_reservation():
    a, b = "2348600000021", "2348600000022"; shopper(a); shopper(b)
    oa = reserve(a, (MILK, 1)); reserve(b, (EGGS, 1))
    call(tools.cancel_order, b, order_id=oa["order_id"])
    assert "couldn't find an open reservation" in S.get_session(b).take_outbox()
    assert S.get_session(b).pending_action is None and status(oa["order_id"]) == "RESERVED"
    try:
        db.cancel_order_db(oa["order_id"], b); raise AssertionError("must refuse")
    except ValueError:
        pass


def test_n4_an_expired_reservation_cannot_be_cancelled():
    p = "2348600000023"; shopper(p); o = reserve(p, (MILK, 1))
    force(o["order_id"], expires="-40 minutes", release="-1 minutes"); db.release_expired()
    call(tools.cancel_order, p)
    assert "don't have any open reservations" in S.get_session(p).take_outbox()


def test_n4_double_yes_cancels_once():
    p = "2348600000024"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 2)); call(tools.cancel_order, p); S.get_session(p).take_outbox()
    A, _ = no_model(); A.run_agent(p, "yes")
    after = stock(MILK)
    fake = FakeAgent(text="Nothing pending."); A = agent(fake)
    assert A.run_agent(p, "yes") == "Nothing pending." and len(fake.calls) == 1
    assert stock(MILK) == after == s0 and status(o["order_id"]) == "CANCELLED"


def test_n4_the_request_expires_and_survives_a_restart():
    p = "2348600000025"; shopper(p); o = reserve(p, (MILK, 1))
    call(tools.cancel_order, p); S.get_session(p).take_outbox()
    restart()
    assert S.get_session(p).pending_action["order_id"] == o["order_id"]
    A, _ = no_model()
    with fake_time(QUOTE_TTL_MINUTES + 1):
        assert "expired" in A.run_agent(p, "yes")
    assert status(o["order_id"]) == "RESERVED"


def test_n4_only_one_open_question_at_a_time():
    p = "2348600000026"; shopper(p); reserve(p, (MILK, 1))
    quote(p, (EGGS, 1)); assert S.get_session(p).pending_quote
    call(tools.cancel_order, p)
    st = S.get_session(p); assert st.pending_action and st.pending_quote is None
    quote(p, (EGGS, 1))
    st = S.get_session(p); assert st.pending_quote and st.pending_action is None


# ---------------- N5: amend ----------------
def test_n5_make_it_3_instead_of_8_changes_the_same_reservation():
    p = "2348600000027"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 8)); assert stock(MILK) == s0 - 8
    assert call(tools.amend_order, p, items=[tools.CartItem(product_id=MILK, quantity=3)]) == orders.ACTION_SHOWN
    shown = S.get_session(p).take_outbox()
    assert "Change reservation" in shown and "8 × Whole Milk 1L" in shown and "3 × Whole Milk 1L" in shown
    assert "does not use another reservation" in shown
    A, boom = no_model(); out = A.run_agent(p, "yes")
    now = db.orders_for_phone(p, 5)
    assert len(now) == 1 and now[0]["order_id"] == o["order_id"], "no second order"
    assert now[0]["pickup_code"] == o["pickup_code"] and now[0]["expires_at"] == o["expires_at"]
    assert now[0]["total_kobo"] == 3 * 220000 and stock(MILK) == s0 - 3 and boom.calls == []
    assert f"Reservation {o['order_id']} updated" in out and f"*{o['pickup_code']}* (unchanged)" in out


def test_n5_amending_up_counts_your_own_units_as_available():
    p = "2348600000028"; shopper(p); set_stock(EGGS, 6)
    o = reserve(p, (EGGS, 4)); assert stock(EGGS) == 2
    ok = call(tools.amend_order, p, items=[tools.CartItem(product_id=EGGS, quantity=6)])
    assert ok == orders.ACTION_SHOWN
    S.get_session(p).take_outbox(); S.get_session(p).clear_action()
    too_many = call(tools.amend_order, p, items=[tools.CartItem(product_id=EGGS, quantity=7)])
    assert "only 6 in stock" in too_many and "unchanged" in too_many and S.get_session(p).pending_action is None
    assert status(o["order_id"]) == "RESERVED" and stock(EGGS) == 2


def test_n5_a_price_change_asks_again_before_changing():
    p = "2348600000029"; shopper(p); o = reserve(p, (LONG, 2))
    call(tools.amend_order, p, items=[tools.CartItem(product_id=LONG, quantity=1)]); S.get_session(p).take_outbox()
    with db.get_conn() as c:
        c.execute("UPDATE branch_inventory SET price_kobo = 500000 WHERE branch_id = ? AND product_id = ?", (LEKKI, LONG))
    try:
        A, _ = no_model(); out = A.run_agent(p, "yes")
        assert "Prices changed" in out and "₦5,000" in out
        assert db.get_order_for_phone(o["order_id"], p)["total_kobo"] == 2 * 480000
        assert "updated" in A.run_agent(p, "yes")
        assert db.get_order_for_phone(o["order_id"], p)["total_kobo"] == 500000
    finally:
        with db.get_conn() as c:
            c.execute("UPDATE branch_inventory SET price_kobo = 480000 WHERE branch_id = ? AND product_id = ?", (LEKKI, LONG))


def test_n5_a_failed_change_leaves_everything_as_it_was():
    p = "2348600000030"; shopper(p); o = reserve(p, (MILK, 2)); s0, e0 = stock(MILK), stock(EGGS)
    items = [{"item": db.get_item(LEKKI, MILK), "quantity": 1}, {"item": db.get_item(LEKKI, EGGS), "quantity": 10 ** 6}]
    try:
        db.amend_order_db(o["order_id"], p, items, db.compute_totals(items)); raise AssertionError("must refuse")
    except ValueError:
        pass
    assert stock(MILK) == s0 and stock(EGGS) == e0
    assert json.loads(db.get_order_for_phone(o["order_id"], p)["items_json"])[0]["quantity"] == 2


def test_n5_an_expired_reservation_cannot_be_changed():
    p = "2348600000031"; shopper(p); s0 = stock(MILK)
    o = reserve(p, (MILK, 2))
    call(tools.amend_order, p, items=[tools.CartItem(product_id=MILK, quantity=1)]); S.get_session(p).take_outbox()
    force(o["order_id"], expires="-40 minutes", release="-1 minutes")
    A, _ = no_model(); out = A.run_agent(p, "yes")       # the turn's sweep expires it first
    assert "not open any more" in out and status(o["order_id"]) == "EXPIRED" and stock(MILK) == s0


def test_n5_asking_for_what_you_already_have_changes_nothing():
    p = "2348600000032"; shopper(p); reserve(p, (MILK, 2))
    out = call(tools.amend_order, p, items=[tools.CartItem(product_id=MILK, quantity=2)])
    assert "already contains exactly that" in out and S.get_session(p).pending_action is None


# ---------------- N6: the model's view and the new guard ----------------
def test_n6_state_note_lists_open_reservations_and_the_right_tools():
    p = "2348600000033"; shopper(p); o = reserve(p, (MILK, 2))
    txt = S.get_session(p).state_message().content
    assert o["order_id"] in txt and "2 × Whole Milk 1L" in txt and "amend_order" in txt and "cancel_order" in txt
    call(tools.cancel_order, p)
    assert "waiting for the customer's YES/NO" in S.get_session(p).state_message().content


def test_n6_a_real_open_order_id_in_a_reply_is_not_flagged():
    p = "2348600000034"; shopper(p); o = reserve(p, (MILK, 1))
    fake = FakeAgent(text=f"You have {o['order_id']} open for pickup."); A = agent(fake)
    assert o["order_id"] in A.run_agent(p, "do i have anything open?") and len(fake.calls) == 1
    fake = FakeAgent(text="You have ORD-ZZZZZZZZ open for pickup."); A = agent(fake)
    assert "ORD-ZZZZZZZZ" not in A.run_agent(p, "and now?") and len(fake.calls) == 4


def test_n6_a_fake_cancel_claim_is_rejected():
    p = "2348600000035"; shopper(p); o = reserve(p, (MILK, 1))
    fake = FakeAgent(text="Done, I've cancelled your order."); A = agent(fake)
    out = A.run_agent(p, "cancel it")
    assert "cancelled" not in out and len(fake.calls) == 4 and status(o["order_id"]) == "RESERVED"
    assert S.get_session(p).last_turn["rejections"] == ["fake_action"] * 4


# ---------------- N7: diagnostics ----------------
def test_n7_diagnostics_record_path_attempts_and_corrections_without_customer_text():
    p = "2348600000036"; S.clear(p)
    def script(inp):
        noticed = any("BRANCH CHECK FAILED" in (m["content"] if isinstance(m, dict) else m.content) for m in inp["messages"])
        return "Which city or area will the pickup happen in?" if noticed else "We have Victoria Island Branch."
    A = agent(FakeAgent(script)); A.run_agent(p, "SECRET-PHRASE-123 which branches?")
    t = S.get_session(p).last_turn
    assert t["path"] == "model" and t["attempts"] == 2 and t["rejections"][0].startswith("branch:") and t["ms"] >= 0
    log = Path(os.environ["LOG_DIR"]) / "turns.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    mine = [r for r in rows if r["attempts"] == 2 and r["rejections"]][-1]
    assert len(mine["who"]) == 8 and p not in log.read_text(encoding="utf-8")
    assert "SECRET-PHRASE-123" not in log.read_text(encoding="utf-8") and "Victoria" not in log.read_text(encoding="utf-8")


def test_n7_code_paths_are_labelled():
    p = "2348600000037"; shopper(p); reserve(p, (MILK, 1))
    call(tools.cancel_order, p); S.get_session(p).take_outbox()
    A, _ = no_model(); A.run_agent(p, "yes")
    assert S.get_session(p).last_turn["path"] == "code:action_yes" and S.get_session(p).last_turn["attempts"] == 0


# ---------------- N8: upgrading an older database ----------------
def test_n8_an_older_database_is_upgraded_in_place_and_stale_holds_are_released():
    old = os.path.join(_TMP, "old.db")
    c = sqlite3.connect(old)
    c.executescript("""
      CREATE TABLE branches (branch_id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, address TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE products (product_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, category TEXT);
      CREATE TABLE branch_inventory (branch_id TEXT NOT NULL, product_id TEXT NOT NULL, price_kobo INTEGER NOT NULL, stock_qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (branch_id, product_id));
      CREATE TABLE orders (order_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, branch_id TEXT NOT NULL, customer_phone TEXT NOT NULL,
        items_json TEXT NOT NULL, subtotal_kobo INTEGER NOT NULL, tax_kobo INTEGER NOT NULL, delivery_kobo INTEGER NOT NULL, total_kobo INTEGER NOT NULL,
        pickup_code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'RESERVED' CHECK (status IN ('RESERVED','READY','COLLECTED','CANCELLED','EXPIRED')),
        created_at TEXT NOT NULL DEFAULT (datetime('now')), expires_at TEXT NOT NULL);
      INSERT INTO branches VALUES ('BR-LKI','Lekki Branch','Lagos','addr',1);
      INSERT INTO products VALUES ('P-2001','Whole Milk 1L','d','Dairy');
      INSERT INTO branch_inventory VALUES ('BR-LKI','P-2001',220000,7);
      INSERT INTO orders VALUES ('ORD-OLD1','k1','BR-LKI','2348000000000','[{"product_id":"P-2001","name":"Whole Milk 1L","quantity":3,"unit_price_kobo":220000}]',660000,0,0,660000,'AAAAAA','RESERVED','2020-01-01 00:00:00','2020-01-01 05:00:00');
    """)
    c.commit(); c.close()
    code = ("from app import db\ndb.init_db()\nc = db.get_conn()\n"
            "cols = [r['name'] for r in c.execute('PRAGMA table_info(orders)')]\n"
            "ok = c.execute(\"SELECT release_at = datetime(expires_at, '+30 minutes') AS ok FROM orders WHERE order_id='ORD-OLD1'\").fetchone()['ok']\n"
            "print('COLS', 'release_at' in cols, 'closed_at' in cols, 'BACKFILL', ok)\n"
            "n = db.release_expired()\n"
            "st = c.execute(\"SELECT status FROM orders WHERE order_id='ORD-OLD1'\").fetchone()['status']\n"
            "stock = c.execute(\"SELECT stock_qty FROM branch_inventory WHERE product_id='P-2001'\").fetchone()['stock_qty']\n"
            "print('RELEASED', n, st, stock)\n"
            "db.init_db(); print('IDEMPOTENT ok')\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                         env={**os.environ, "DATABASE_PATH": old}).stdout
    assert "COLS True True BACKFILL 1" in out and "RELEASED 1 EXPIRED 10" in out and "IDEMPOTENT ok" in out, out


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            failed += 1; print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
