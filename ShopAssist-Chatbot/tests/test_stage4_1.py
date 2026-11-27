"""Stage 4.1 verification: the brain is a setting (hosted / backup / local), with spend and abuse guards.

Why (from live chats): a 14B model on this PC took 5-8 minutes per turn. A hosted small model
answers in seconds for fractions of a naira, but only if it can never run up a bill, never be
spammed into one, and never leave a customer hanging when a provider has a bad day.
Run:  python tests/test_stage4_1.py      (no network, no Ollama, no API keys: everything is faked)

NEEDS
  N1  lanes are plain settings ("provider:model"); bad settings never stop the app
  N2  hosted lane -> retry -> backup -> local; failures are classified; bad lanes pause
  N3  spend is counted in integer micro-dollars; alerts once a day; paid lanes stop at the cap
  N4  every turn records lane, tokens, cost and model time (never customer text)
  N5  per-phone turn limit, message length cap, and a limited mode when no brain is available
  N6  the branch is named once, not in every answer
  N7  local model options, provider objects and warm-up behave
"""
import asyncio, contextlib, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp()
os.environ["SHOPASSIST_NO_DOTENV"] = "1"   # tests never read your private .env
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_shop41.db")
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
for k in ("LLM_PRIMARY", "LLM_BACKUP", "LLM_LOCAL", "LLM_PRICE_PRIMARY", "LLM_PRICE_BACKUP"):
    os.environ.pop(k, None)
os.environ["ANTHROPIC_API_KEY"] = "test-key-anthropic"
os.environ["GROQ_API_KEY"] = "test-key-groq"

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage   # noqa: E402

from app import branches, budget, db, llm, orders, tools                      # noqa: E402
from app import session as S                                                  # noqa: E402

db.init_db()
PRIMARY = llm.Lane("primary", "anthropic", "claude-haiku-5-5")     # known price $0.10 / $0.50 per M
BACKUP = llm.Lane("backup", "groq", "llama-3.3-70b-versatile")      # unknown price -> assumed $1 / $3
LOCAL = llm.Lane("local", "ollama", "llama3.1:8b")                  # free
LANES = {"primary": PRIMARY, "backup": BACKUP, "local": LOCAL}


# ---------------- helpers ----------------
class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, seconds): self.t += seconds


class RateLimitError(Exception): status_code = 429
class AuthError(Exception): status_code = 401
class BillingError(Exception): status_code = 402
class Overloaded(Exception): status_code = 529
class UpstreamTimeout(Exception): pass


def ai(text="ok", i=3000, o=100):
    return AIMessage(content=text, usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o})


class Graph:
    """A fake model graph. Each outcome is a reply text, an exception to raise, or a callable."""
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
    def invoke(self, inp, config=None):
        self.calls.append(list(inp["messages"]))
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(o, Exception): raise o
        msg = o(inp) if callable(o) else ai(o)
        return {"messages": list(inp["messages"]) + [msg]}


def make_router(order, clock=None, **graphs):
    return llm.LaneRouter(prompt="p", tools=[], lanes=[LANES[s] for s in order],
                          graph_builder=lambda lane: graphs[lane.slot],
                          clock=clock or Clock(), sleep=lambda s: None)


@contextlib.contextmanager
def fake_day(day):
    old = budget.today; budget.today = lambda: day
    try: yield
    finally: budget.today = old


@contextlib.contextmanager
def cap(usd):
    old = budget.DAILY_BUDGET_USD; budget.DAILY_BUDGET_USD = usd
    try: yield
    finally: budget.DAILY_BUDGET_USD = old


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try: yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@contextlib.contextmanager
def webhook():
    """Capture operator alerts instead of sending them."""
    sent, old_url, old_post = [], budget.HUMAN_ESCALATION_WEBHOOK, budget.httpx.post
    budget.HUMAN_ESCALATION_WEBHOOK = "http://alerts.example/hook"
    budget.httpx.post = lambda url, json=None, timeout=None: sent.append(json)
    try: yield sent
    finally: budget.HUMAN_ESCALATION_WEBHOOK, budget.httpx.post = old_url, old_post


class fake_time:
    def __init__(self, minutes): self.minutes = minutes
    def __enter__(self): self._o = S._now; S._now = lambda: self._o() + self.minutes * 60
    def __exit__(self, *a): S._now = self._o


MSGS = [HumanMessage(content="hi"), AIMessage(content="hello"), HumanMessage(content="how much is rice?")]


class FakeAgent:
    """Stands in for the router inside agent.py (it can also report `last` usage and errors)."""
    def __init__(self, text="ok", raises=None, last=None, errors=None):
        self.calls, self.text, self.raises, self.last, self.last_errors = [], text, raises, last, errors or []
    def invoke(self, inp):
        self.calls.append(list(inp["messages"]))
        if self.raises: raise self.raises
        return {"messages": list(inp["messages"]) + [AIMessage(content=self.text)]}


def agent(fake):
    from app import agent as A
    A.agent = fake
    return A


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def shopper(phone):
    S.clear(phone)
    call(tools.set_pickup_location, phone, place="Lekki")
    branches.confirm_proposal(S.get_session(phone)); S.get_session(phone).take_outbox()


# ---------------- N1: lane settings ----------------
def test_n1_parse_lane():
    l = llm.parse_lane("primary", "anthropic:claude-haiku-5-5")
    assert (l.slot, l.provider, l.model, l.hosted) == ("primary", "anthropic", "claude-haiku-5-5", True)
    o = llm.parse_lane("local", "ollama:llama3.1:8b")
    assert o.model == "llama3.1:8b" and not o.hosted, "the model name may itself contain ':'"
    assert llm.parse_lane("backup", "") is None and llm.parse_lane("backup", "   ") is None
    assert o.label == "local:ollama:llama3.1:8b"


def test_n1_bad_lane_settings_explain_themselves():
    for spec, needle in [("claude-haiku", "provider:model"), ("anthropic:", "provider:model"), ("bard:x", "unknown provider")]:
        try:
            llm.parse_lane("primary", spec); raise AssertionError(spec)
        except ValueError as e:
            assert needle in str(e), (spec, str(e))


def test_n1_configured_lanes_keep_order_and_skip_bad_ones():
    old = (llm.LLM_PRIMARY, llm.LLM_BACKUP, llm.LLM_LOCAL)
    try:
        llm.LLM_PRIMARY, llm.LLM_BACKUP, llm.LLM_LOCAL = "anthropic:claude-haiku-5-5", "nonsense", "ollama:llama3.1:8b"
        assert [l.slot for l in llm.configured_lanes()] == ["primary", "local"]
        llm.LLM_PRIMARY, llm.LLM_BACKUP, llm.LLM_LOCAL = "", "", ""
        assert llm.configured_lanes() == []
    finally:
        llm.LLM_PRIMARY, llm.LLM_BACKUP, llm.LLM_LOCAL = old


def test_n1_a_hosted_lane_without_a_key_is_simply_off():
    r = make_router(["primary", "backup", "local"], primary=Graph("a"), backup=Graph("b"), local=Graph("c"))
    assert [l.slot for l in r.available_lanes()] == ["primary", "backup", "local"]
    with env(ANTHROPIC_API_KEY=None):
        assert [l.slot for l in r.available_lanes()] == ["backup", "local"]
        assert any("OFF (ANTHROPIC_API_KEY not set)" in line for line in r.describe())


def test_n1_describe_shows_prices_and_flags_guesses():
    lines = make_router(["primary", "backup", "local"], primary=Graph("a"), backup=Graph("b"), local=Graph("c")).describe()
    assert "$0.1/$0.5 per M tokens" in lines[0] and "ASSUMED" not in lines[0]
    assert "ASSUMED" in lines[1] and "LLM_PRICE_BACKUP" in lines[1]
    assert lines[2].endswith("free")


# ---------------- N2: routing and failure handling ----------------
def test_n2_primary_answers_and_the_caller_keeps_valid_indexes():
    p = Graph("from primary"); r = make_router(["primary", "local"], primary=p, local=Graph("x"))
    out = r.invoke({"messages": MSGS})
    assert out["messages"][:3] == MSGS and out["messages"][-1].content == "from primary"
    assert r.last.lane.slot == "primary" and r.last.in_tokens == 3000 and r.last.out_tokens == 100
    assert r.last.cost_micro == 350 and r.last_errors == []


def test_n2_a_slow_primary_is_retried_once_then_the_backup_answers():
    p = Graph(UpstreamTimeout("took too long")); b = Graph("backup here")
    r = make_router(["primary", "backup"], primary=p, backup=b)
    out = r.invoke({"messages": MSGS})
    assert len(p.calls) == 2 and len(b.calls) == 1 and out["messages"][-1].content == "backup here"
    assert r.last.lane.slot == "backup"
    assert r.last_errors == [(PRIMARY.label, "transient"), (PRIMARY.label, "transient")]


def test_n2_a_transient_blip_is_healed_by_the_retry():
    p = Graph(Overloaded("overloaded"), "second try works"); b = Graph("backup")
    r = make_router(["primary", "backup"], primary=p, backup=b)
    assert r.invoke({"messages": MSGS})["messages"][-1].content == "second try works"
    assert len(b.calls) == 0 and r.last.lane.slot == "primary"


def test_n2_rate_limit_moves_on_at_once_and_the_lane_rests_a_minute():
    clock = Clock(); p = Graph(RateLimitError("slow down")); b = Graph("backup")
    r = make_router(["primary", "backup"], clock, primary=p, backup=b)
    r.invoke({"messages": MSGS})
    assert len(p.calls) == 1, "no retry on a rate limit"
    assert [l.slot for l in r.available_lanes()] == ["backup"]
    clock.advance(61)
    assert [l.slot for l in r.available_lanes()] == ["primary", "backup"]


def test_n2_a_bad_key_or_no_credit_pauses_the_lane_and_tells_the_operator_once():
    for exc, kind in [(AuthError("bad key"), "auth"), (BillingError("credit balance is too low"), "billing")]:
        clock = Clock(); p = Graph(exc); b = Graph("backup")
        r = make_router(["primary", "backup"], clock, primary=p, backup=b)
        with fake_day(f"2031-01-0{1 if kind == 'auth' else 2}"), webhook() as sent:
            r.invoke({"messages": MSGS}); r.invoke({"messages": MSGS})
            assert len(p.calls) == 1, "paused: not retried on the next turn"
            assert [s["type"] for s in sent] == [f"lane_{kind}:{PRIMARY.label}"]
            clock.advance(1801)
            assert PRIMARY in r.available_lanes()


def test_n2_when_every_lane_fails_the_caller_gets_laneunavailable():
    r = make_router(["primary", "local"], primary=Graph(AuthError("x")), local=Graph(UpstreamTimeout("t")))
    try:
        r.invoke({"messages": MSGS}); raise AssertionError("should fail")
    except llm.LaneUnavailable as e:
        assert [k for _, k in e.tried] == ["auth", "transient"] and "no model lane available" in str(e)


def test_n2_an_unexpected_error_skips_to_the_next_lane_without_retrying():
    p = Graph(ValueError("malformed request")); b = Graph("backup")
    r = make_router(["primary", "backup"], primary=p, backup=b)
    r.invoke({"messages": MSGS}); assert len(p.calls) == 1 and r.last.lane.slot == "backup"


def test_n2_three_transient_failures_in_a_row_pause_a_lane_and_success_resets_the_count():
    clock = Clock(); p = Graph(Overloaded("o")); b = Graph("backup")
    r = make_router(["primary", "backup"], clock, primary=p, backup=b)
    r.invoke({"messages": MSGS})                       # two failed tries
    assert PRIMARY in r.available_lanes()
    r.invoke({"messages": MSGS})                       # third failure trips the breaker
    assert PRIMARY not in r.available_lanes()
    clock.advance(61); assert PRIMARY in r.available_lanes()
    p2 = Graph(Overloaded("o"), "ok"); r2 = make_router(["primary"], clock, primary=p2)
    r2.invoke({"messages": MSGS}); assert r2._fails[PRIMARY.label] == 0


def test_n2_state_note_is_folded_into_the_customer_message_for_hosted_lanes_only():
    note = SystemMessage(content="STATE NOTE", name=S.STATE_MSG_NAME)
    msgs = [HumanMessage(content="hi"), AIMessage(content="yo"), note, HumanMessage(content="price?")]
    hosted, local = Graph("h"), Graph("l")
    r = make_router(["primary"], primary=hosted); out = r.invoke({"messages": msgs})
    sent = hosted.calls[0]
    assert not any(isinstance(m, SystemMessage) for m in sent), "providers reject a mid-conversation system message"
    assert "<system_state>\nSTATE NOTE\n</system_state>\n\nprice?" in sent[-1].content
    assert out["messages"][:4] == msgs, "the caller still sees its own messages at the same positions"
    r = make_router(["local"], local=local); r.invoke({"messages": msgs})
    assert local.calls[0] == msgs, "the local lane gets the original shape"


def test_n2_fold_state_note_edge_cases():
    assert llm.fold_state_note([HumanMessage(content="x")])[0].content == "x"
    tail = llm.fold_state_note([SystemMessage(content="N", name=S.STATE_MSG_NAME)])
    assert len(tail) == 1 and "<system_state>" in tail[0].content


def test_n2_classify_table():
    C = llm.classify
    assert C(AuthError("x")) == "auth" and C(BillingError("x")) == "billing" and C(RateLimitError("x")) == "rate_limit"
    assert C(Overloaded("x")) == "transient" and C(UpstreamTimeout("x")) == "transient" and C(ConnectionError("x")) == "transient"
    assert C(ValueError("x")) == "other" and C(Exception("Error: invalid api key")) == "auth"
    assert C(Exception("Your credit balance is too low")) == "billing"


# ---------------- N3: spend control ----------------
def test_n3_cost_is_integer_micro_dollars():
    assert budget.cost_micro(PRIMARY, 3000, 100) == 350
    assert budget.cost_micro(BACKUP, 3000, 100) == 3300, "unknown hosted price is assumed pessimistically"
    assert budget.cost_micro(LOCAL, 10 ** 6, 10 ** 6) == 0
    with env(LLM_PRICE_BACKUP="0,0"): assert budget.cost_micro(BACKUP, 3000, 100) == 0
    with env(LLM_PRICE_BACKUP="0.5,1.5"): assert budget.cost_micro(BACKUP, 1000, 1000) == 2000
    with env(LLM_PRICE_BACKUP="garbage"): assert budget.cost_micro(BACKUP, 1000, 0) == 1000


def test_n3_usage_accumulates_per_day_and_lane():
    with fake_day("2032-01-01"):
        budget.record(PRIMARY, 3000, 100); budget.record(PRIMARY, 1000, 50); budget.record(LOCAL, 500, 20)
        rows = {r["lane"]: r for r in db.llm_usage_rows("2032-01-01")}
        assert rows[PRIMARY.label]["calls"] == 2 and rows[PRIMARY.label]["input_tokens"] == 4000
        assert rows[PRIMARY.label]["cost_micro_usd"] == 350 + 125 and rows[LOCAL.label]["cost_micro_usd"] == 0
        assert budget.spent_micro() == 475
    with fake_day("2032-01-02"): assert budget.spent_micro() == 0


def test_n3_alerts_fire_once_at_the_percentage_and_once_at_the_cap():
    with fake_day("2033-01-01"), cap(0.0004), webhook() as sent:
        budget.record(PRIMARY, 3000, 100)          # 350 of 400 = 87%
        budget.record(PRIMARY, 3000, 100)          # 700 of 400 = over the cap
        budget.record(PRIMARY, 3000, 100)          # more spend, no more alerts
        assert [s["type"] for s in sent] == ["budget_alert", "budget_exhausted"]
        assert "88%" in sent[0]["text"] and "$0.0004" in sent[0]["text"]     # 350 of 400 = 87.5%
        assert "$0.0004" in sent[1]["text"]


def test_n3_at_the_cap_paid_lanes_stop_but_the_local_lane_keeps_working():
    with fake_day("2034-01-01"), cap(0.0004), webhook():
        budget.record(PRIMARY, 3000, 100); budget.record(PRIMARY, 3000, 100)
        assert budget.exhausted()
        r = make_router(["primary", "backup", "local"], primary=Graph("p"), backup=Graph("b"), local=Graph("from local"))
        assert [l.slot for l in r.available_lanes()] == ["local"]
        assert r.invoke({"messages": MSGS})["messages"][-1].content == "from local"
    with fake_day("2034-01-02"), cap(0.0004):
        assert not budget.exhausted() and PRIMARY in r.available_lanes(), "a new day starts fresh"


def test_n3_the_budget_day_is_the_shops_lagos_day():
    import datetime as dt, re
    class FakeDT:
        @staticmethod
        def now(tz=None):
            return dt.datetime(2030, 5, 1, 23, 30, tzinfo=dt.timezone.utc).astimezone(tz)
    old = budget.datetime; budget.datetime = FakeDT
    try:
        assert budget.today() == "2030-05-02", "23:30 UTC is already the next day in Lagos (UTC+1)"
    finally:
        budget.datetime = old
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", budget.today())


def test_n3_a_budget_of_zero_means_no_cap():
    with fake_day("2035-01-01"), cap(0):
        budget.record(PRIMARY, 10 ** 7, 10 ** 7); assert not budget.exhausted()


def test_n3_accounting_can_never_break_a_reply():
    old = db.add_llm_usage
    db.add_llm_usage = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    try:
        assert budget.record(PRIMARY, 1000, 10) == 0
        r = make_router(["primary"], primary=Graph("still answers"))
        assert r.invoke({"messages": MSGS})["messages"][-1].content == "still answers"
    finally:
        db.add_llm_usage = old


# ---------------- N4: usage and diagnostics ----------------
def test_n4_usage_from_provider_counts_and_ollama_timings():
    m1 = AIMessage(content="a", usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})
    m2 = AIMessage(content="b", usage_metadata={"input_tokens": 120, "output_tokens": 20, "total_tokens": 140})
    assert llm.usage_from([m1, m2, HumanMessage(content="x")])[:2] == (220, 30)
    ol = AIMessage(content="c", response_metadata={"load_duration": 2_000_000_000, "prompt_eval_duration": 3_000_000_000,
                                                    "eval_duration": 1_000_000_000, "prompt_eval_count": 1900, "eval_count": 40})
    assert llm.usage_from([ol]) == (1900, 40, 2000, 3000, 1000)


def test_n4_missing_token_counts_are_estimated_for_hosted_lanes():
    r = make_router(["primary"], primary=Graph(lambda inp: AIMessage(content="x" * 400)))
    r.invoke({"messages": MSGS}); assert r.last.in_tokens > 0 and r.last.out_tokens == 100 and r.last.cost_micro > 0


def test_n4_turn_diagnostics_carry_lane_tokens_cost_and_speed_but_no_customer_text():
    p = "2348700000001"; S.clear(p)
    use = llm.Use(PRIMARY, 1234, 3000, 100, 350, 0, 0, 0)
    A = agent(FakeAgent(text="Which city?", last=use, errors=[(BACKUP.label, "rate_limit")]))
    A.run_agent(p, "SECRET-WORDS-777 hello")
    t = S.get_session(p).last_turn
    assert t["lane"] == PRIMARY.label and t["model_ms"] == 1234 and t["in_tokens"] == 3000 and t["out_tokens"] == 100
    assert t["cost_micro_usd"] == 350 and t["lane_errors"] == [f"{BACKUP.label}:rate_limit"]
    log = (Path(os.environ["LOG_DIR"]) / "turns.jsonl").read_text(encoding="utf-8")
    last = json.loads(log.splitlines()[-1])
    assert last["cost_micro_usd"] == 350 and "SECRET-WORDS-777" not in log and p not in log


def test_n4_new_turn_has_all_the_fields():
    from app import diagnostics
    assert {"lane", "model_ms", "in_tokens", "out_tokens", "cost_micro_usd", "lane_errors"} <= set(diagnostics.new_turn())


# ---------------- N5: abuse guards and limited mode ----------------
def test_n5_the_per_hour_turn_limit_stops_the_model_not_the_code_paths():
    p = "2348700000002"; shopper(p)
    fake = FakeAgent(text="ok"); A = agent(fake); A.MODEL_TURNS_PER_HOUR = 3
    try:
        for i in range(3): assert A.run_agent(p, f"question {i}") == "ok"
        before = len(S.get_session(p).messages)
        assert A.run_agent(p, "question 4") == A.RATE_LIMIT_TEXT and len(fake.calls) == 3
        assert len(S.get_session(p).messages) == before, "the refused message is not left in history"
        assert S.get_session(p).last_turn["path"] == "rate_limited"
        call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
        assert "Order reserved" in A.run_agent(p, "yes"), "YES to a quote is code: never rate limited"
        with fake_time(61):
            assert A.run_agent(p, "question 5") == "ok", "the window rolls after an hour"
    finally:
        A.MODEL_TURNS_PER_HOUR = 30


def test_n5_the_turn_count_survives_a_restart():
    p = "2348700000003"; S.clear(p)
    A = agent(FakeAgent()); A.run_agent(p, "one"); A.run_agent(p, "two")
    S._cache.clear()
    assert S.get_session(p).model_turns_last_hour() == 2


def test_n5_a_very_long_message_is_cut_before_the_model_sees_it():
    p = "2348700000004"; S.clear(p)
    fake = FakeAgent(); A = agent(fake); A.MAX_MESSAGE_CHARS = 50
    try:
        A.run_agent(p, "x" * 2000)
        assert len(fake.calls[0][-1].content) == 50 and S.get_session(p).last_turn["truncated"] is True
        assert all(len(m.content) <= 50 for m in S.get_session(p).messages if m.type == "human")
    finally:
        A.MAX_MESSAGE_CHARS = 600


def test_n5_with_no_brain_available_the_customer_gets_the_limited_mode_message():
    p = "2348700000005"; S.clear(p)
    err = llm.LaneUnavailable([(PRIMARY.label, "transient"), (LOCAL.label, "transient")])
    fake = FakeAgent(raises=err); A = agent(fake)
    with fake_day("2036-01-01"), webhook() as sent:
        assert A.run_agent(p, "how much is rice") == A.LIMITED_MODE_MESSAGE
        assert A.run_agent(p, "hello?") == A.LIMITED_MODE_MESSAGE
        assert [s["type"] for s in sent] == ["limited_mode"], "the operator is told once a day"
    t = S.get_session(p).last_turn
    assert t["path"] == "limited" and f"{PRIMARY.label}:transient" in t["lane_errors"]
    assert not any(m.type == "human" for m in S.get_session(p).messages), "unanswered messages are not kept"
    assert "busy" in A.LIMITED_MODE_MESSAGE


def test_n5_limited_mode_can_still_show_an_order_the_customer_names():
    p = "2348700000006"; shopper(p)
    call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
    orders.confirm_pending(S.get_session(p)); oid = db.orders_for_phone(p, 1)[0]["order_id"]
    A = agent(FakeAgent(raises=llm.LaneUnavailable([])))
    with fake_day("2036-01-02"), webhook():
        out = A.run_agent(p, f"where is {oid.lower()} please")
        assert out.startswith("Here's what I can see:") and oid in out and "RESERVED" in out
        assert A.run_agent(p, "where is ORD-ZZZZZZZZ") == A.LIMITED_MODE_MESSAGE


def test_n5_confirmations_and_quotes_keep_working_with_no_model_at_all():
    p = "2348700000007"; shopper(p)
    call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=2)])
    A = agent(FakeAgent(raises=llm.LaneUnavailable([])))
    out = A.run_agent(p, "yes")
    assert "Order reserved" in out and S.get_session(p).last_turn["path"] == "code:quote_yes"


def test_n5_after_a_code_confirmed_branch_a_dead_model_still_confirms_the_branch():
    p = "2348700000008"; S.clear(p)
    call(tools.set_pickup_location, p, place="Lekki"); S.get_session(p).take_outbox()
    A = agent(FakeAgent(raises=llm.LaneUnavailable([])))
    with fake_day("2036-01-03"), webhook():
        out = A.run_agent(p, "yes")
    assert out.startswith("Pickup branch confirmed: *Lekki Branch*") and A.LIMITED_MODE_MESSAGE in out
    assert S.get_session(p).branch_id == "BR-LKI"


# ---------------- N6: name the branch once ----------------
def test_n6_the_branch_is_named_in_the_first_answer_then_dropped():
    p = "2348700000009"; shopper(p)
    first = call(tools.search_products, p, keyword="rice")
    second = call(tools.search_products, p, keyword="milk")
    assert first.startswith("At Lekki Branch:") and not second.startswith("At ") and "Whole Milk 1L" in second
    call(tools.set_pickup_location, p, place="Ikeja"); branches.confirm_proposal(S.get_session(p))
    assert call(tools.search_products, p, keyword="rice").startswith("At Ikeja Branch:"), "a new branch is named again"
    other = "2348700000010"; shopper(other)
    assert call(tools.search_products, other, keyword="rice").startswith("At Lekki Branch:"), "per customer"


def test_n6_quotes_and_receipts_always_name_the_branch():
    p = "2348700000011"; shopper(p)
    call(tools.search_products, p, keyword="rice"); call(tools.search_products, p, keyword="milk")
    call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
    assert "*Order summary — Lekki Branch*" in S.get_session(p).take_outbox()
    assert "Order reserved at Lekki Branch" in orders.confirm_pending(S.get_session(p))


def test_n6_the_prompt_tells_the_model_not_to_repeat_the_branch():
    import re
    from app import agent as A
    assert "do not repeat its name" in A.SYSTEM_PROMPT
    assert set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", A.SYSTEM_PROMPT)) <= {t.name for t in tools.ALL_TOOLS}


# ---------------- N7: local options, providers, warm-up, entry points ----------------
def test_n7_the_local_model_gets_a_context_window_and_keep_alive():
    from langchain_ollama import ChatOllama
    m = llm.make_chat_model(LOCAL)
    assert m.num_ctx == llm.OLLAMA_NUM_CTX and m.keep_alive == llm.OLLAMA_KEEP_ALIVE and m.num_predict == llm.LLM_MAX_OUTPUT_TOKENS
    q = llm.make_chat_model(llm.Lane("local", "ollama", "qwen3:14b"))
    if "think" in ChatOllama.model_fields:
        assert q.think is False


def test_n7_options_an_older_package_does_not_know_are_dropped_not_fatal():
    class Old:
        model_fields = {"model": object(), "temperature": object()}
    assert llm._supported_kwargs(Old, {"model": "m", "temperature": 0, "num_ctx": 1, "client_kwargs": {}}) == {"model": "m", "temperature": 0}


def test_n7_hosted_providers_build_with_the_right_settings():
    try:
        import langchain_anthropic, langchain_openai       # noqa: F401
    except ImportError:
        print("  (skipped: langchain-anthropic / langchain-openai not installed)"); return
    a = llm.make_chat_model(PRIMARY)
    assert a.model == "claude-haiku-5-5" and a.max_retries == 0 and a.max_tokens == llm.LLM_MAX_OUTPUT_TOKENS
    g = llm.make_chat_model(BACKUP)
    assert "api.groq.com" in str(g.openai_api_base) and g.model_name == "llama-3.3-70b-versatile"
    with env(GROQ_API_KEY=None):
        try:
            llm.make_chat_model(BACKUP); raise AssertionError("needs a key")
        except llm.MissingKey as e:
            assert "GROQ_API_KEY" in str(e)


def test_n7_warm_up_only_loads_the_local_model_when_it_is_the_first_lane():
    loaded = []
    class M:
        def invoke(self, text): loaded.append(text)
    old = llm.make_chat_model; llm.make_chat_model = lambda lane: M()
    try:
        assert make_router(["local"], local=Graph("x")).warm_up() is True and loaded == ["hi"]
        loaded.clear()
        assert make_router(["primary", "local"], primary=Graph("x"), local=Graph("y")).warm_up() is False and loaded == []
    finally:
        llm.make_chat_model = old


def test_n7_server_startup_lists_the_lanes_and_does_not_warm_up_by_default():
    from app import main
    asyncio.run(main.startup())
    assert not hasattr(main.app.state, "warm") and main.LLM_WARM_UP is False


def test_n7_chat_starts_lists_the_lanes_and_quiet_hides_the_developer_lines():
    out = subprocess.run([sys.executable, "-m", "chat", "--quiet"], cwd=str(ROOT), input="quit\n", capture_output=True, text=True,
                         env={**os.environ, "DATABASE_PATH": os.path.join(_TMP, "chat.db")}, timeout=60)
    assert out.returncode == 0 and "Model lanes:" in out.stdout and "Bye!" in out.stdout, out.stdout + out.stderr


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            import traceback; failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
