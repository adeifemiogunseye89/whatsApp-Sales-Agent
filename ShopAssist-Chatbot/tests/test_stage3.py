"""Stage 3 verification: orders are created by CODE, from a quote the customer confirmed.

Why: in a live chat the model wrote "The total is 58,000 - should I place this
order?" (no items, no branch) and the model alone decided that "yes" meant go.
Run:  python tests/test_stage3.py      (no Ollama needed: the LLM is a fake)

NEEDS
  N1  the model cannot place orders (no create_order tool, not in the prompt)
  N2  the customer sees a CODE-written, itemised quote (items, unit prices, branch, total)
  N3  a clean YES places the order WITHOUT involving the model; a clean NO cancels
  N4  only plain answers count as YES/NO ("yes but make it 3" goes to the model)
  N5  quotes expire, and price/stock changes are caught at the moment of YES
  N6  the same basket can be re-ordered; a double YES never makes two orders
  N7  a quote never survives a branch change, and survives a restart
  N8  WhatsApp formatting is fixed and the ground-truth guard still works
"""
import os, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shop3.db")

from langchain_core.messages import AIMessage        # noqa: E402

from app import db, orders, tools, textfmt           # noqa: E402
from app import session as S                         # noqa: E402
from app.config import QUOTE_TTL_MINUTES             # noqa: E402

db.init_db()
LEKKI = "BR-LKI"


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def restart():
    S._cache.clear()


class fake_time:
    def __init__(self, minutes): self.minutes = minutes
    def __enter__(self):
        self._o = S._now; S._now = lambda: self._o() + self.minutes * 60
    def __exit__(self, *a): S._now = self._o


class FakeAgent:
    """Stands in for the LLM. `script(inp)` may call real tools, then returns the model's text."""
    def __init__(self, script=None, text="Sure!"):
        self.calls, self.script, self.text = [], script, text
    def invoke(self, inp):
        self.calls.append(list(inp["messages"]))
        text = self.script(inp) if self.script else self.text
        return {"messages": list(inp["messages"]) + [AIMessage(content=text)]}


def agent_mod(fake):
    from app import agent as A
    A.agent = fake
    return A


def shopper(phone, place="Lekki"):
    S.clear(phone)
    call(tools.set_pickup_location, phone, place=place)
    call(tools.confirm_pickup_branch, phone, accepted=True)


def quote(phone, *lines):
    items = [tools.CartItem(product_id=p, quantity=q) for p, q in lines]
    return call(tools.get_price_quote, phone, items=items)


def stock(branch, pid):
    return db.get_item(branch, pid)["stock_qty"]


def order_count(phone):
    return len(db.orders_for_phone(phone, limit=100))


# ---------------- N1 ----------------
def test_n1_model_has_no_way_to_place_an_order():
    from app import agent as A
    assert "create_order" not in {t.name for t in tools.ALL_TOOLS}
    assert "create_order" not in A.SYSTEM_PROMPT and not hasattr(tools, "create_order")


# ---------------- N2 ----------------
def test_n2_quote_is_itemised_and_code_written():
    p = "2348400000001"; shopper(p)
    out = quote(p, ("P-1001", 4), ("P-2001", 2))
    assert out.startswith("QUOTE PREPARED") and "₦" not in out, "model gets no numbers to paraphrase"
    shown = S.get_session(p).outbox
    assert "*Order summary — Lekki Branch*" in shown
    assert "4 × Royal Basmati Rice 5kg @ ₦14,500 = ₦58,000" in shown
    assert "2 × Whole Milk 1L @ ₦2,200 = ₦4,400" in shown
    assert "*Total: ₦62,400* (pay at pickup)" in shown
    assert "Demo address, Lekki Phase 1" in shown and "Reply *YES*" in shown and "*NO*" in shown
    assert f"valid for {QUOTE_TTL_MINUTES} minutes" in shown
    assert "**" not in shown, "WhatsApp bold is a single asterisk"


def test_n2_duplicate_lines_are_merged():
    p = "2348400000002"; shopper(p)
    quote(p, ("P-1002", 2), ("p-1002", 1))
    assert "3 × Long Grain Rice 2kg" in S.get_session(p).outbox
    assert len(S.get_session(p).pending_quote["items"]) == 1


def test_n2_quote_refused_for_unstocked_or_oversized():
    p = "2348400000003"; shopper(p)
    assert "Could not quote" in quote(p, ("P-1001", 99))      # far more than the shelf holds
    assert S.get_session(p).pending_quote is None and S.get_session(p).outbox is None
    assert "maximum is 99" in quote(p, ("P-1002", 60), ("P-1002", 60))


def test_n2_run_agent_shows_the_code_text_not_the_models():
    p = "2348400000004"; shopper(p)
    def script(inp):
        quote(p, ("P-1001", 4))
        return "The total for your order is 58k. Should I place it?"   # model paraphrase
    A = agent_mod(FakeAgent(script))
    reply = A.run_agent(p, "4 units of basmati rice")
    assert reply.startswith("*Order summary — Lekki Branch*") and "58k" not in reply
    last = S.get_session(p).messages[-1]
    assert last.type == "ai" and last.content == reply, "history records what the customer saw"


# ---------------- N3 ----------------
def test_n3_clean_yes_places_order_without_the_model():
    p = "2348400000005"; shopper(p)
    s0 = stock(LEKKI, "P-1001")
    A = agent_mod(FakeAgent(lambda inp: (quote(p, ("P-1001", 2)), "ok")[1]))
    A.run_agent(p, "2 basmati please")
    boom = FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("model must not run")))
    A = agent_mod(boom)
    reply = A.run_agent(p, "yes please place the order")
    assert boom.calls == []
    m = re.search(r"Order ID: \*(ORD-[A-Z0-9]+)\*\nPickup code: \*([A-Z0-9]{6})\*", reply)
    assert m and "2 × Royal Basmati Rice 5kg" in reply and "₦29,000" in reply and "Lekki Branch" in reply
    assert stock(LEKKI, "P-1001") == s0 - 2 and order_count(p) == 1
    assert S.get_session(p).pending_quote is None


def test_n3_clean_no_cancels():
    p = "2348400000006"; shopper(p)
    s0 = stock(LEKKI, "P-1001")
    A = agent_mod(FakeAgent(lambda inp: (quote(p, ("P-1001", 1)), "ok")[1]))
    A.run_agent(p, "one basmati")
    boom = agent_mod(FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("no model"))))
    assert "cancelled" in boom.run_agent(p, "no thanks")
    assert order_count(p) == 0 and stock(LEKKI, "P-1001") == s0 and S.get_session(p).pending_quote is None


# ---------------- N4 ----------------
def test_n4_only_plain_answers_are_yes_no():
    yes = ["yes", "Yes!", "yeah", "ok", "okay", "sure", "yes please", "please place the order",
           "go ahead", "abeg go ahead", "confirm", "ehen", "oya place it", "yes, place the order"]
    no = ["no", "No.", "nope", "no thanks", "cancel", "cancel the order", "i dont want it",
          "don't want it", "stop", "not now"]
    other = ["yes but make it 3", "yes 3", "no, change to 3 units", "how much is it?", "what time is pickup",
             "place the order for 5", "i want to place a different order", "yes no", "hello",
             "make it 3 units", "is it ok?", ""]
    for t in yes: assert orders.parse_confirmation(t) == "yes", t
    for t in no: assert orders.parse_confirmation(t) == "no", t
    for t in other: assert orders.parse_confirmation(t) is None, t


def test_n4_unclear_reply_goes_to_the_model_and_keeps_the_quote():
    p = "2348400000007"; shopper(p)
    A = agent_mod(FakeAgent(lambda inp: (quote(p, ("P-1001", 1)), "x")[1]))
    A.run_agent(p, "one basmati")
    qid = S.get_session(p).pending_quote["id"]
    fake = FakeAgent(text="Sure, how many would you like?")
    A = agent_mod(fake)
    assert A.run_agent(p, "yes but make it 3") == "Sure, how many would you like?"
    assert len(fake.calls) == 1 and S.get_session(p).pending_quote["id"] == qid and order_count(p) == 0
    assert "waiting for the customer's YES/NO" in fake.calls[0][-2].content


def test_n4_branch_yes_no_is_not_stolen_by_a_pending_quote():
    p = "2348400000008"; shopper(p)
    A = agent_mod(FakeAgent(lambda inp: (quote(p, ("P-1001", 1)), "x")[1]))
    A.run_agent(p, "one basmati")
    call(tools.set_pickup_location, p, place="Wuse")           # branch question now open
    fake = FakeAgent(text="model handles the branch answer")
    A = agent_mod(fake)
    assert A.run_agent(p, "yes") == "model handles the branch answer"
    assert fake.calls and order_count(p) == 0


# ---------------- N5 ----------------
def test_n5_expired_quote_cannot_be_confirmed():
    p = "2348400000009"; shopper(p)
    quote(p, ("P-1001", 1)); s0 = stock(LEKKI, "P-1001")
    with fake_time(QUOTE_TTL_MINUTES + 1):
        out = orders.confirm_pending(S.get_session(p))
    assert "expired" in out and order_count(p) == 0 and stock(LEKKI, "P-1001") == s0
    assert S.get_session(p).pending_quote is None


def test_n5_price_change_forces_a_fresh_look():
    p = "2348400000010"; shopper(p)
    quote(p, ("P-1002", 1)); old_id = S.get_session(p).pending_quote["id"]
    with db.get_conn() as c:
        c.execute("UPDATE branch_inventory SET price_kobo = 500000 WHERE branch_id=? AND product_id='P-1002'", (LEKKI,))
    try:
        out = orders.confirm_pending(S.get_session(p))
        assert "Prices changed" in out and "₦5,000" in out and order_count(p) == 0
        new = S.get_session(p).pending_quote
        assert new["id"] != old_id and new["totals"]["total"] == 500000
        assert "Order reserved" in orders.confirm_pending(S.get_session(p))     # new YES at new price
    finally:
        with db.get_conn() as c:
            c.execute("UPDATE branch_inventory SET price_kobo = 480000 WHERE branch_id=? AND product_id='P-1002'", (LEKKI,))


def test_n5_stock_gone_before_yes():
    p = "2348400000011"; shopper(p)
    quote(p, ("P-1003", 3))
    with db.get_conn() as c:
        c.execute("UPDATE branch_inventory SET stock_qty = 1 WHERE branch_id=? AND product_id='P-1003'", (LEKKI,))
    out = orders.confirm_pending(S.get_session(p))
    assert "only 1 of Jasmine Rice 5kg left" in out and order_count(p) == 0
    assert S.get_session(p).pending_quote is None


# ---------------- N6 ----------------
def test_n6_same_basket_can_be_reordered_later():
    p = "2348400000012"; shopper(p); s0 = stock(LEKKI, "P-2001")
    for _ in range(2):
        quote(p, ("P-2001", 2))
        assert "Order reserved" in orders.confirm_pending(S.get_session(p))
    ids = {o["order_id"] for o in db.orders_for_phone(p)}
    assert len(ids) == 2 and stock(LEKKI, "P-2001") == s0 - 4


def test_n6_double_yes_creates_one_order():
    p = "2348400000013"; shopper(p)
    A = agent_mod(FakeAgent(lambda inp: (quote(p, ("P-2001", 1)), "x")[1]))
    A.run_agent(p, "one milk")
    first = A.run_agent(p, "yes")
    fake = FakeAgent(text="Nothing pending - what would you like?")
    A = agent_mod(fake)
    A.run_agent(p, "yes")                                       # second tap
    assert "Order reserved" in first and order_count(p) == 1


def test_n6_quote_ids_are_unique():
    p = "2348400000014"; shopper(p)
    ids = set()
    for _ in range(40):
        quote(p, ("P-2001", 1)); ids.add(S.get_session(p).pending_quote["id"])
    assert len(ids) == 40


# ---------------- N7 ----------------
def test_n7_quote_survives_restart_then_confirms():
    p = "2348400000015"; shopper(p)
    quote(p, ("P-1002", 2)); qid = S.get_session(p).pending_quote["id"]
    restart()
    assert S.get_session(p).pending_quote["id"] == qid
    boom = agent_mod(FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("no model"))))
    assert "Order reserved" in boom.run_agent(p, "yes")


def test_n7_branch_change_voids_the_quote():
    p = "2348400000016"; shopper(p)
    quote(p, ("P-1002", 1))
    call(tools.set_pickup_location, p, place="Ikeja")
    call(tools.confirm_pickup_branch, p, accepted=True)
    assert S.get_session(p).pending_quote is None
    assert "nothing waiting" in orders.confirm_pending(S.get_session(p)).lower()


def test_n7_outbox_is_never_saved():
    p = "2348400000017"; shopper(p)
    quote(p, ("P-1002", 1))
    assert S.get_session(p).outbox
    restart()
    assert S.get_session(p).outbox is None


# ---------------- N8 ----------------
def test_n8_whatsapp_formatting():
    assert textfmt.to_whatsapp("**Order ID:** ORD-1") == "*Order ID:* ORD-1"
    assert textfmt.to_whatsapp("# Summary\nTotal **₦5**") == "*Summary*\nTotal *₦5*"
    assert textfmt.to_whatsapp("plain *already ok*") == "plain *already ok*"


def test_n8_model_markdown_is_converted_on_the_way_out():
    p = "2348400000018"; S.clear(p)
    A = agent_mod(FakeAgent(text="At **Lekki Branch** basmati is **₦14,500**"))
    assert A.run_agent(p, "hi") == "At *Lekki Branch* basmati is *₦14,500*"


def test_n8_model_cannot_fake_an_order():
    p = "2348400000019"; S.clear(p)
    A = agent_mod(FakeAgent(text="Done! Your order ORD-ZZZZZZZZ is reserved. Pickup code: ABC123"))
    reply = A.run_agent(p, "yes place it")
    assert "ORD-ZZZZZZZZ" not in reply and "ABC123" not in reply


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            import traceback; failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
