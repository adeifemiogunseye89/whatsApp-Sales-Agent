"""Stage 2 verification: persistent session state + safe history + real handoff.

Run from the project root:   python tests/test_stage2.py
Needs NO Ollama and NO WhatsApp (the LLM is replaced by a tiny fake).

Stage-2 NEEDS proved here:
  N1  branch lock, proposal, order key and chat history survive a restart
  N2  idle chats are forgotten after SESSION_TTL_HOURS (and old rows purged)
  N3  trimmed history never starts on a tool call/result
  N4  handoff is real: bot goes silent, expires, and never silences on a failed alert
  N5  a corrupt saved session can never lock a customer out
"""
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shop2.db")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from app import db, tools                                     # noqa: E402
from app import session as S                                  # noqa: E402
from app.config import HANDOFF_HOURS, SESSION_TTL_HOURS       # noqa: E402

db.init_db()
LEKKI = "BR-LKI"


def restart():
    """Simulate a server restart: memory is wiped, the database survives."""
    S._cache.clear()


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


class fake_time:
    """Context manager: pretend `hours` have passed."""
    def __init__(self, hours):
        self.hours = hours
    def __enter__(self):
        self._orig = S._now
        S._now = lambda: self._orig() + self.hours * 3600
    def __exit__(self, *a):
        S._now = self._orig


def lock(phone, place="Lekki"):
    call(tools.set_pickup_location, phone, place=place)
    call(tools.confirm_pickup_branch, phone, accepted=True)


# ---------------- N1: survives restart ----------------
def test_n1_branch_lock_survives_restart():
    p = "2348100000001"; S.clear(p)
    lock(p)
    restart()
    assert S.get_session(p).branch_id == LEKKI
    assert "At Lekki Branch" in call(tools.search_products, p, keyword="rice")


def test_n1_pending_proposal_survives_restart():
    p = "2348100000002"; S.clear(p)
    call(tools.set_pickup_location, p, place="Wuse")
    restart()
    assert S.get_session(p).proposed_branch_id == "BR-WUS"
    assert "locked" in call(tools.confirm_pickup_branch, p, accepted=True)


def test_n1_order_key_survives_restart():
    # Stage 3: an order now comes from confirming a quote; its id is the key.
    from app import orders
    p = "2348100000003"; S.clear(p)
    lock(p)
    cart = [tools.CartItem(product_id="P-1002", quantity=1)]
    call(tools.get_price_quote, p, items=cart)
    assert "Order reserved" in orders.confirm_pending(S.get_session(p))
    restart()
    assert S.get_session(p).last_order_key is not None
    assert "ORD-" in call(tools.track_order, p)


def test_n1_history_roundtrip_keeps_tool_calls():
    p = "2348100000004"; S.clear(p)
    s = S.get_session(p)
    s.set_history([
        HumanMessage(content="hi"),
        AIMessage(content="", tool_calls=[{"name": "search_products", "args": {"keyword": "rice"}, "id": "c1"}]),
        ToolMessage(content="At Lekki Branch: ...", tool_call_id="c1"),
        AIMessage(content="We have rice."),
    ])
    restart()
    m = S.get_session(p).messages
    assert [x.type for x in m] == ["human", "ai", "tool", "ai"]
    assert m[1].tool_calls[0]["name"] == "search_products" and m[2].tool_call_id == "c1"


def test_clear_deletes_row():
    p = "2348100000005"; S.clear(p)
    lock(p)
    assert db.load_session_json(p) is not None
    S.clear(p)
    assert db.load_session_json(p) is None and S.get_session(p).branch_id is None


# ---------------- N2: idle expiry ----------------
def test_n2_idle_session_is_forgotten_in_memory():
    p = "2348100000006"; S.clear(p)
    lock(p)
    with fake_time(SESSION_TTL_HOURS + 1):
        assert S.get_session(p).branch_id is None
    assert db.load_session_json(p) is None


def test_n2_idle_session_is_forgotten_after_restart():
    p = "2348100000007"; S.clear(p)
    lock(p)
    restart()
    with fake_time(SESSION_TTL_HOURS + 1):
        assert S.get_session(p).branch_id is None


def test_n2_fresh_session_is_kept():
    p = "2348100000008"; S.clear(p)
    lock(p)
    restart()
    with fake_time(SESSION_TTL_HOURS - 1):
        assert S.get_session(p).branch_id == LEKKI


def test_n2_purge_removes_only_old_rows():
    old, new = "2348100000009", "2348100000010"
    for p in (old, new):
        S.clear(p); lock(p)
    with db.get_conn() as c:
        c.execute("UPDATE sessions SET updated_at = datetime('now', '-30 hours') WHERE phone = ?", (old,))
    restart()
    removed = S.purge_expired()
    assert removed >= 1
    assert db.load_session_json(old) is None and db.load_session_json(new) is not None


# ---------------- N3: safe trimming ----------------
def _random_history(rng, turns):
    msgs, n = [], 0
    for _ in range(turns):
        msgs.append(HumanMessage(content=f"u{n}"))
        for _ in range(rng.randint(0, 3)):
            n += 1
            msgs.append(AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": f"c{n}"}]))
            msgs.append(ToolMessage(content="r", tool_call_id=f"c{n}"))
        msgs.append(AIMessage(content=f"a{n}"))
        n += 1
    return msgs


def _structurally_valid(msgs):
    if not msgs or msgs[0].type != "human":
        return False
    seen = set()
    for m in msgs:
        if m.type == "ai":
            seen.update(tc["id"] for tc in m.tool_calls)
        if m.type == "tool" and m.tool_call_id not in seen:
            return False
    return True


def test_n3_trim_never_starts_mid_toolcall():
    rng = random.Random(7)
    for _ in range(300):
        hist = _random_history(rng, rng.randint(1, 12))
        cap = rng.randint(1, 15)
        out = S.trim_history(hist, cap)
        assert _structurally_valid(out), (cap, [m.type for m in hist], [m.type for m in out])
        assert out[-1] is hist[-1], "newest message must always be kept"


def test_n3_set_history_trims_and_saves():
    p = "2348100000011"; S.clear(p)
    hist = _random_history(random.Random(1), 25)
    S.get_session(p).set_history(hist)
    restart()
    saved = S.get_session(p).messages
    assert len(saved) < len(hist) and _structurally_valid(saved)


# ---------------- N4: handoff ----------------
class FakeAgent:
    """Stands in for the LangGraph agent. Records what it was given."""
    def __init__(self):
        self.calls = []
    def invoke(self, inp):
        self.calls.append(inp["messages"])
        return {"messages": list(inp["messages"]) + [AIMessage(content="Hello!")]}


def _install_fake_agent():
    from app import agent as A
    fake = FakeAgent(); A.agent = fake
    return A, fake


def test_n4_handoff_silences_the_bot_and_survives_restart():
    A, fake = _install_fake_agent()
    p = "2348100000012"; S.clear(p); lock(p)
    out = call(tools.escalate_to_human, p, reason="refund")      # no webhook = dev mode
    assert "human agent" in out and S.get_session(p).handoff_active
    restart()
    assert A.run_agent(p, "hello??") is None and fake.calls == [], "LLM must not run"
    assert S.get_session(p).messages[-1].content == "hello??", "message still recorded"
    S.get_session(p).release_handoff()
    assert A.run_agent(p, "hi again") == "Hello!"


def test_n4_handoff_expires():
    p = "2348100000013"; S.clear(p)
    call(tools.escalate_to_human, p, reason="x")
    assert S.get_session(p).handoff_active
    with fake_time(HANDOFF_HOURS + 0.1):
        assert not S.get_session(p).handoff_active


def test_n4_failed_alert_does_not_silence_bot():
    p = "2348100000014"; S.clear(p)
    old_url, old_post = tools.HUMAN_ESCALATION_WEBHOOK, tools.httpx.post
    tools.HUMAN_ESCALATION_WEBHOOK = "http://alerts.invalid/hook"
    def boom(*a, **k): raise RuntimeError("network down")
    tools.httpx.post = boom
    try:
        out = call(tools.escalate_to_human, p, reason="complaint")
    finally:
        tools.HUMAN_ESCALATION_WEBHOOK, tools.httpx.post = old_url, old_post
    assert out.startswith("ESCALATION FAILED") and not S.get_session(p).handoff_active


def test_n4_alert_payload_contains_context():
    p = "2348100000015"; S.clear(p); lock(p)
    S.get_session(p).set_history([HumanMessage(content="my order is wrong")])
    sent = {}
    class R:
        def raise_for_status(self): pass
    def fake_post(url, json=None, timeout=None): sent.update(url=url, json=json); return R()
    old_url, old_post = tools.HUMAN_ESCALATION_WEBHOOK, tools.httpx.post
    tools.HUMAN_ESCALATION_WEBHOOK = "http://alerts.example/hook"; tools.httpx.post = fake_post
    try:
        call(tools.escalate_to_human, p, reason="wrong item")
    finally:
        tools.HUMAN_ESCALATION_WEBHOOK, tools.httpx.post = old_url, old_post
    j = sent["json"]
    assert j["phone"] == p and j["reason"] == "wrong item" and j["branch_id"] == LEKKI
    assert j["recent_messages"][-1]["content"] == "my order is wrong"
    assert S.get_session(p).handoff_active


# ---------------- end of turn + N5 ----------------
def test_end_of_turn_history_is_persisted_and_starts_with_human():
    A, fake = _install_fake_agent()
    p = "2348100000016"; S.clear(p)
    for i in range(40):                       # long chat: forces trimming
        assert A.run_agent(p, f"msg {i}") == "Hello!"
    first_seen = fake.calls[-1][0]
    assert first_seen.type == "human", "model must never receive a history starting mid-exchange"
    restart()
    assert S.get_session(p).messages[-1].content == "Hello!"
    assert len(S.get_session(p).messages) <= 20


def test_n5_corrupt_saved_state_starts_fresh():
    p = "2348100000017"; S.clear(p)
    db.save_session_json(p, "{this is not json")
    restart()
    s = S.get_session(p)
    assert s.branch_id is None and s.messages == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"PASS  {name}")
        except Exception as e:
            import traceback; failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
