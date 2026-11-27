"""Stage 4.2 verification: lane hardening, built from what went wrong in live chats.

Live evidence (Groq free tier, gpt-oss-120b):
  * a 429 "try again in 2.5s" was labelled BILLING, so the lane was locked for 30 minutes
  * a turn that failed half-way left a branch proposal nobody saw (a later "yes" would lock it)
  * the model called set_pickup_location with "city or area" (words from the prompt) and,
    twice, with "Lekki" (an example in the tool description) that the customer never typed
  * a first message took 9 s: the lane was loaded on the customer's time
Run:  python tests/test_stage4_2.py      (no network, no keys: everything is faked)

NEEDS
  N1  rate limits are never mistaken for billing; the provider's suggested wait is read
  N2  a short wait is served inside the turn; a long one rests the lane; billing still pauses it
  N3  a failed turn's side effects are undone (between attempts and when no lane answers)
  N4  the branch tool only accepts places the customer actually typed
  N5  `python -m check_lanes` tells you in plain English what to fix
  N6  lanes load at start-up; failed attempts are timed in the diagnostics
  N7  docs and settings no longer suggest the retired model
"""
import asyncio, contextlib, io, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp()
os.environ["SHOPASSIST_NO_DOTENV"] = "1"   # tests never read your private .env
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_shop42.db")
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
for k in ("LLM_PRIMARY", "LLM_BACKUP", "LLM_LOCAL", "LLM_PRICE_PRIMARY", "LLM_PRICE_BACKUP"):
    os.environ.pop(k, None)
os.environ["ANTHROPIC_API_KEY"] = "test-key-anthropic"
os.environ["GROQ_API_KEY"] = "test-key-groq"

from langchain_core.messages import AIMessage      # noqa: E402

import check_lanes                                 # noqa: E402
from app import branches, db, llm, tools           # noqa: E402
from app import session as S                       # noqa: E402
from app.session import append_message            # noqa: E402

db.init_db()
PRIMARY = llm.Lane("primary", "anthropic", "claude-haiku-5-5")
BACKUP = llm.Lane("backup", "groq", "openai/gpt-oss-120b")
LOCAL = llm.Lane("local", "ollama", "llama3.1:8b")
LANES = {"primary": PRIMARY, "backup": BACKUP, "local": LOCAL}
GROQ_429 = ("Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier "
            "`on_demand` on tokens per minute (TPM): Limit 8000, Used 5919, Requested 2416. Please try again in 2.5125s. "
            "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")


# ---------------- helpers ----------------
class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, seconds): self.t += seconds


class Err(Exception):
    def __init__(self, message="", status=None, headers=None):
        super().__init__(message); self.status_code = status
        if headers is not None: self.response = type("R", (), {"headers": headers})()


def ai(text="ok", i=3000, o=100):
    return AIMessage(content=text, usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o})


class Graph:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []
    def invoke(self, inp, config=None):
        self.calls.append(list(inp["messages"]))
        o = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(o, Exception): raise o
        msg = o(inp) if callable(o) else ai(o)
        return {"messages": list(inp["messages"]) + [msg]}


def make_router(order, clock=None, sleeps=None, **graphs):
    sleeps = sleeps if sleeps is not None else []
    return llm.LaneRouter(prompt="p", tools=[], lanes=[LANES[s] for s in order],
                          graph_builder=lambda lane: graphs[lane.slot],
                          clock=clock or Clock(), sleep=sleeps.append)


MSGS = [llm.HumanMessage(content="hi")]


@contextlib.contextmanager
def fake_day(day):
    from app import budget
    old = budget.today; budget.today = lambda: day
    try: yield
    finally: budget.today = old


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
def as_model(session):
    session.model_turn_active = True
    try: yield
    finally: session.model_turn_active = False


class FakeAgent:
    def __init__(self, script=None, text="ok", last=None, errors=None, timings=None):
        self.script, self.text, self.last, self.last_errors, self.last_timings = script, text, last, errors or [], timings or []
        self.inputs = []
    def invoke(self, inp):
        self.inputs.append(inp)
        out = self.script(inp) if self.script else self.text
        return {"messages": list(inp["messages"]) + [AIMessage(content=out)]}


def agent(fake):
    from app import agent as A
    A.agent = fake
    return A


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def fresh(phone):
    S.clear(phone)
    return S.get_session(phone)


def said(session, *texts):
    for t in texts:
        append_message(session, "user", t)


def lock(phone, place="Lekki"):
    s = fresh(phone)
    call(tools.set_pickup_location, phone, place=place)
    branches.confirm_proposal(s); s.take_outbox()
    s.branch_just_confirmed = False            # a LATER turn: the flag only lives for the confirming turn
    return s


# ---------------- N1: classification and wait parsing ----------------
def test_n1_a_rate_limit_that_mentions_billing_is_still_a_rate_limit():
    e = Err(GROQ_429, 429)
    assert llm.classify(e) == "rate_limit" and llm.retry_after(e) == 2.5125


def test_n1_real_billing_problems_are_still_billing():
    assert llm.classify(Err("Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing", 400)) == "billing"
    assert llm.classify(Err("You exceeded your current quota, please check your plan and billing details", 429)) == "billing"
    assert llm.classify(Err("payment required", 402)) == "billing"


def test_n1_the_other_kinds_are_unchanged():
    assert llm.classify(Err("bad", 401)) == "auth" and llm.classify(Err("Error: Invalid API key")) == "auth"
    assert llm.classify(Err("The model `x` does not exist or you do not have access to it.", 404)) == "other"
    assert llm.classify(Err("overloaded", 529)) == "transient" and llm.classify(ConnectionError("x")) == "transient"


def test_n1_wait_formats():
    cases = {"Please try again in 2.5125s.": 2.5125, "try again in 7m12.5s": 432.5, "try again in 350ms": 0.35,
             "try again in 2m": 120.0, "try again in 45s": 45.0, "try again in 1m30s": 90.0, "slow down": None,
             "try again in a bit": None}
    for text, want in cases.items():
        assert llm.retry_after(Err(text)) == want, (text, llm.retry_after(Err(text)), want)
    assert llm.retry_after(Err("x", 429, headers={"retry-after": "5"})) == 5.0


# ---------------- N2: waiting out and resting ----------------
def test_n2_a_short_rate_limit_is_waited_out_and_retried_in_the_same_turn():
    sleeps = []; p = Graph(Err(GROQ_429, 429), "answered after the wait"); b = Graph("backup")
    r = make_router(["primary", "backup"], sleeps=sleeps, primary=p, backup=b)
    out = r.invoke({"messages": MSGS})
    assert out["messages"][-1].content == "answered after the wait" and len(p.calls) == 2 and len(b.calls) == 0
    assert len(sleeps) == 1 and abs(sleeps[0] - 2.8125) < 0.01, sleeps
    assert r.last.lane.slot == "primary" and PRIMARY in r.available_lanes(), "not paused: the wait was served"


def test_n2_a_long_rate_limit_rests_the_lane_for_the_time_asked():
    clock = Clock(); sleeps = []
    r = make_router(["primary", "backup"], clock, sleeps, primary=Graph(Err("try again in 45s", 429)), backup=Graph("b"))
    r.invoke({"messages": MSGS})
    assert sleeps == [] and [l.slot for l in r.available_lanes()] == ["backup"]
    clock.advance(30); assert [l.slot for l in r.available_lanes()] == ["backup"]
    clock.advance(17); assert PRIMARY in r.available_lanes(), "46 seconds = the 45 asked for + 1"


def test_n2_a_rest_is_never_longer_than_two_minutes():
    clock = Clock()
    r = make_router(["primary", "backup"], clock, primary=Graph(Err("try again in 7m12s", 429)), backup=Graph("b"))
    r.invoke({"messages": MSGS}); clock.advance(121)
    assert PRIMARY in r.available_lanes()


def test_n2_the_wait_is_served_only_once_per_turn():
    sleeps = []
    r = make_router(["primary", "backup"], sleeps=sleeps, primary=Graph(Err("try again in 2s", 429)), backup=Graph("b"))
    out = r.invoke({"messages": MSGS})
    assert len(sleeps) == 1 and out["messages"][-1].content == "b" and r.last.lane.slot == "backup"
    assert r.last_errors == [(PRIMARY.label, "rate_limit")] * 2


def test_n2_with_no_suggested_wait_the_lane_rests_sixty_seconds():
    clock = Clock(); r = make_router(["primary", "backup"], clock, primary=Graph(Err("slow down", 429)), backup=Graph("b"))
    r.invoke({"messages": MSGS}); assert PRIMARY not in r.available_lanes()
    clock.advance(61); assert PRIMARY in r.available_lanes()


def test_n2_billing_still_pauses_a_lane_for_thirty_minutes():
    clock = Clock()
    with fake_day("2040-01-01"):
        r = make_router(["primary", "backup"], clock, primary=Graph(Err("credit balance is too low", 400)), backup=Graph("b"))
        r.invoke({"messages": MSGS}); clock.advance(1700); assert PRIMARY not in r.available_lanes()
        clock.advance(101); assert PRIMARY in r.available_lanes()


# ---------------- N3: undoing a failed turn ----------------
def _messy(s):
    s.proposed_branch_id = "BR-WUS"; s.pending_quote = {"id": "Q-1", "items": [], "totals": {}, "expires_at": 9e12, "branch_id": s.branch_id}
    s.pending_action = {"type": "cancel", "order_id": "ORD-X", "expires_at": 9e12}
    s.branch_announced = True; s.listed_branch_ids = ["BR-IKJ", "BR-LKI"]


def test_n3_snapshot_and_restore_roundtrip():
    s = lock("2348800000001"); before = s.snapshot()
    _messy(s); s.set_outbox("never shown"); s.save()
    S._cache.clear(); assert S.get_session("2348800000001").proposed_branch_id == "BR-WUS", "the mess really is in the database"
    s = S.get_session("2348800000001")
    s.restore(before)
    assert s.proposed_branch_id is None and s.pending_quote is None and s.pending_action is None
    assert s.branch_announced is False and s.listed_branch_ids == [] and s.outbox is None
    S._cache.clear()
    r = S.get_session("2348800000001")
    assert r.proposed_branch_id is None and r.pending_quote is None, "restore is saved"


def test_n3_a_snapshot_is_a_copy_not_a_view():
    s = lock("2348800000002"); s.pending_quote = {"id": "Q-A", "items": [], "totals": {}, "expires_at": 9e12, "branch_id": s.branch_id}
    snap = s.snapshot(); s.pending_quote["id"] = "Q-CHANGED"
    s.restore(snap); assert s.pending_quote["id"] == "Q-A"


def test_n3_a_failed_turn_leaves_no_stray_branch_proposal():
    p = "2348800000003"; fresh(p)
    def script(inp):
        call(tools.set_pickup_location, p, place="Lekki")          # customer typed 'lekki': allowed, proposes
        assert S.get_session(p).proposed_branch_id == "BR-LKI"
        raise llm.LaneUnavailable([(PRIMARY.label, "rate_limit")], [900])
    A = agent(FakeAgent(script))
    with fake_day("2040-02-01"):
        assert A.run_agent(p, "lekki") == A.LIMITED_MODE_MESSAGE
    s = S.get_session(p)
    assert s.proposed_branch_id is None, "a later 'yes' must not lock a branch the customer never saw"
    assert s.outbox is None and s.last_turn["path"] == "limited"


def test_n3_a_quote_prepared_by_a_failed_turn_is_discarded():
    p = "2348800000004"; lock(p)
    def script(inp):
        call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
        raise llm.LaneUnavailable([(PRIMARY.label, "transient")])
    A = agent(FakeAgent(script))
    with fake_day("2040-02-02"):
        A.run_agent(p, "one milk please")
    assert S.get_session(p).pending_quote is None


def test_n3_a_successful_turn_keeps_what_its_tools_did():
    p = "2348800000005"; lock(p)
    A = agent(FakeAgent(lambda inp: (call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)]), "x")[1]))
    A.run_agent(p, "one milk please")
    assert S.get_session(p).pending_quote is not None


def test_n3_the_router_undoes_between_attempts_and_before_the_next_lane():
    calls = []
    p = Graph(Err("overloaded", 529)); b = Graph("backup ok")
    r = make_router(["primary", "backup"], primary=p, backup=b)
    r.invoke({"messages": MSGS, "before_retry": lambda: calls.append(1)})
    assert len(calls) == 2, "not before the first attempt; before the retry and before the backup"


def test_n3_no_undo_when_the_first_attempt_succeeds_and_a_broken_undo_cannot_break_a_turn():
    calls = []
    make_router(["primary"], primary=Graph("fine")).invoke({"messages": MSGS, "before_retry": lambda: calls.append(1)})
    assert calls == []
    r = make_router(["primary", "backup"], primary=Graph(Err("overloaded", 529)), backup=Graph("still works"))
    def boom(): raise RuntimeError("undo exploded")
    assert r.invoke({"messages": MSGS, "before_retry": boom})["messages"][-1].content == "still works"


def test_n3_run_agent_hands_the_router_a_working_undo():
    p = "2348800000006"; s = lock(p)
    fake = FakeAgent(lambda inp: (call(tools.set_pickup_location, p, place="Ikeja"), "ok")[1]); A = agent(fake)
    A.run_agent(p, "ikeja")
    assert S.get_session(p).proposed_branch_id == "BR-IKJ"
    fake.inputs[0]["before_retry"]()
    assert S.get_session(p).proposed_branch_id is None


# ---------------- N4: the branch tool only takes places the customer typed ----------------
def test_n4_the_tool_description_has_no_example_places():
    d = tools.set_pickup_location.description
    assert not any(w in d for w in ("Abuja", "Lekki", "Ikeja", "Lagos")), "models copy examples into calls"
    assert "customer" in d.lower() and "never an example" in d.lower()


def test_n4_a_placeholder_becomes_the_standard_question_not_a_branch_search():
    p = "2348800000007"; s = fresh(p); said(s, "do u have basmati rice")
    with as_model(s):
        out = call(tools.set_pickup_location, p, place="city or area")
    assert out == tools.BRANCH_SHOWN and s.take_outbox() == branches.ask_place_text() and s.proposed_branch_id is None


def test_n4_a_made_up_place_is_rejected_and_a_confirmed_branch_is_left_alone():
    p = "2348800000008"; s = fresh(p); said(s, "ikeja")
    with as_model(s):
        call(tools.set_pickup_location, p, place="Lekki")
    assert s.take_outbox() == branches.ask_place_text() and s.proposed_branch_id is None
    s = lock("2348800000009", "Ikeja"); said(s, "how much is rice")
    with as_model(s):
        out = call(tools.set_pickup_location, "2348800000009", place="Lekki")
    assert "stays as it is" in out and s.outbox is None and s.proposed_branch_id is None and s.branch_id == "BR-IKJ"


def test_n4_places_the_customer_typed_are_accepted_even_with_typos():
    p = "2348800000010"; s = fresh(p); said(s, "branches in lagos")
    with as_model(s): call(tools.set_pickup_location, p, place="Lagos")
    assert "Which branch would you like" in s.take_outbox()
    said(s, "ikeja branch")
    with as_model(s): call(tools.set_pickup_location, p, place="Ikeja Branch")
    assert s.proposed_branch_id == "BR-IKJ"; s.take_outbox()
    said(s, "any branch at vitoria ?")
    with as_model(s): call(tools.set_pickup_location, p, place="Victoria Island")
    assert "couldn't find a branch for 'Victoria Island'" in s.take_outbox()


def test_n4_recent_messages_count_not_only_the_last_one():
    p = "2348800000011"; s = fresh(p); said(s, "lekki", "how much is rice")
    with as_model(s): call(tools.set_pickup_location, p, place="Lekki")
    assert s.proposed_branch_id == "BR-LKI"


def test_n4_a_number_or_ordinal_picks_from_the_list_that_was_shown():
    p = "2348800000012"; s = fresh(p); said(s, "lagos")
    with as_model(s): call(tools.set_pickup_location, p, place="Lagos")
    s.take_outbox(); assert s.listed_branch_ids == ["BR-IKJ", "BR-LKI"]
    for reply, place, want in [("2", "Lekki Branch", "BR-LKI"), ("1", "Ikeja Branch", "BR-IKJ"),
                               ("the second one", "Lekki Branch", "BR-LKI"), ("last", "Lekki Branch", "BR-LKI")]:
        said(s, reply)
        with as_model(s): call(tools.set_pickup_location, p, place=place)
        assert s.proposed_branch_id == want, (reply, place); s.take_outbox()
        s.listed_branch_ids = ["BR-IKJ", "BR-LKI"]
    said(s, "2")
    with as_model(s): call(tools.set_pickup_location, p, place="Ikeja Branch")
    assert s.take_outbox() == branches.ask_place_text(), "'2' means Lekki, so Ikeja is a mistake"
    said(s, "I have 2 kids and want rice")
    with as_model(s): call(tools.set_pickup_location, p, place="Lekki Branch")
    assert s.take_outbox() == branches.ask_place_text(), "a long sentence is not a pick"


def test_n4_the_list_choice_survives_a_restart():
    p = "2348800000013"; s = fresh(p); said(s, "lagos")
    with as_model(s): call(tools.set_pickup_location, p, place="Lagos")
    S._cache.clear()
    assert S.get_session(p).listed_branch_ids == ["BR-IKJ", "BR-LKI"]


def test_n4_right_after_a_code_confirmed_branch_the_tool_is_refused():
    p = "2348800000014"; s = fresh(p); said(s, "lekki"); s.branch_just_confirmed = True
    with as_model(s): out = call(tools.set_pickup_location, p, place="Lekki")
    assert out == tools.JUST_CONFIRMED and s.proposed_branch_id is None and s.outbox is None


def test_n4_direct_system_calls_are_not_checked_only_the_model_is():
    p = "2348800000015"; s = fresh(p)
    call(tools.set_pickup_location, p, place="Lekki")                # no model turn active: trusted
    assert s.proposed_branch_id == "BR-LKI"


def test_n4_words_like_branch_or_area_alone_are_not_a_place():
    p = "2348800000016"; s = fresh(p); said(s, "which branch is near the area please")
    for place in ("the branch", "area", "location please", "nearest branch"):
        with as_model(s): call(tools.set_pickup_location, p, place=place)
        assert s.take_outbox() == branches.ask_place_text(), place


def test_n4_replay_after_yes_the_model_cannot_propose_another_branch():
    p = "2348800000017"; fresh(p)
    A = agent(FakeAgent(lambda inp: (call(tools.set_pickup_location, p, place="Ikeja"), "x")[1])); A.run_agent(p, "ikeja")
    assert S.get_session(p).proposed_branch_id == "BR-IKJ"
    def resume(inp):
        call(tools.set_pickup_location, p, place="Lekki")            # the incident: an example value, unprompted
        return "We have Mamador oil and rice. What would you like?"
    A = agent(FakeAgent(resume))
    out = A.run_agent(p, "yes")
    s = S.get_session(p)
    assert out.startswith("Pickup branch confirmed: *Ikeja Branch*") and "Would you like" not in out
    assert "Is that right" not in out and s.branch_id == "BR-IKJ" and s.proposed_branch_id is None


def test_n4_replay_placeholder_on_the_first_message():
    p = "2348800000018"; fresh(p)
    A = agent(FakeAgent(lambda inp: (call(tools.set_pickup_location, p, place="city or area"), "x")[1]))
    out = A.run_agent(p, "do u have basmati rice")
    assert out == branches.ask_place_text() and "'city or area'" not in out


# ---------------- N5: check_lanes ----------------
class Model:
    def __init__(self, reply=None, error=None): self.reply, self.error = reply, error
    def invoke(self, text):
        if self.error: raise self.error
        return self.reply


def factory_for(spec):
    def f(lane):
        v = spec[lane.slot]
        if isinstance(v, Exception) and isinstance(v, (llm.MissingKey, llm.MissingPackage)): raise v
        return Model(error=v) if isinstance(v, Exception) else Model(reply=v)
    return f


def test_n5_a_working_lane_reports_speed_tokens_and_cost():
    r = check_lanes.check_lane(PRIMARY, factory_for({"primary": ai("OK", 30, 4)}), lister=lambda l: [])
    assert r["ok"] and r["in"] == 30 and r["out"] == 4 and r["cost_micro"] == 5 and r["text"] == "OK"


def test_n5_every_failure_gets_a_plain_english_fix():
    L = lambda lane: ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    def fix(lane, err): return check_lanes.check_lane(lane, factory_for({lane.slot: err}), L)["fix"]
    assert "ANTHROPIC_API_KEY" in fix(PRIMARY, llm.MissingKey("x")) and ".env" in fix(PRIMARY, llm.MissingKey("x"))
    assert "pip install langchain-anthropic" in fix(PRIMARY, llm.MissingPackage("run:  pip install langchain-anthropic"))
    assert "rejected the key" in fix(PRIMARY, Err("bad", 401))
    assert "no usable credit" in fix(PRIMARY, Err("Your credit balance is too low", 400)) and "Claude Pro" in fix(PRIMARY, Err("credit balance is too low", 400))
    assert "limit was reached" in fix(BACKUP, Err(GROQ_429, 429)) and "2.5" in fix(BACKUP, Err(GROQ_429, 429))
    nf = fix(BACKUP, Err("The model `x` does not exist or you do not have access to it.", 404))
    assert "cannot use the model name 'openai/gpt-oss-120b'" in nf and "qwen/qwen3.8-27b" in nf
    ol = fix(LOCAL, ConnectionError("refused"))
    assert "Ollama is not answering" in ol and "ollama list" in ol
    assert "network problem" in fix(BACKUP, Err("overloaded", 529))


def test_n5_model_listing_uses_each_providers_endpoint():
    seen = []
    def get(url, headers=None):
        seen.append((url, headers or {}))
        return {"models": [{"name": "llama3.1:8b"}]} if "/api/tags" in url else {"data": [{"id": "b"}, {"id": "a"}]}
    assert check_lanes.list_models(PRIMARY, get) == ["a", "b"]
    assert seen[-1][0] == "https://api.anthropic.com/v1/models" and seen[-1][1]["x-api-key"] == "test-key-anthropic" and "anthropic-version" in seen[-1][1]
    assert check_lanes.list_models(BACKUP, get) == ["a", "b"] and seen[-1][0] == "https://api.groq.com/openai/v1/models"
    assert seen[-1][1]["Authorization"] == "Bearer test-key-groq"
    assert check_lanes.list_models(llm.Lane("backup", "gemini", "g"), get) and seen[-1][0].endswith("/openai/models")
    assert check_lanes.list_models(LOCAL, get) == ["llama3.1:8b"] and seen[-1][0].endswith("/api/tags")
    assert check_lanes.list_models(PRIMARY, lambda u, h=None: (_ for _ in ()).throw(RuntimeError("down"))) == []


def test_n5_run_reports_each_lane_and_sets_the_exit_code():
    buf = []
    ok = check_lanes.run([PRIMARY, BACKUP], factory_for({"primary": ai("OK", 30, 4), "backup": Err("bad", 401)}), lambda l: [], out=buf.append)
    text = "\n".join(buf)
    assert ok == 0 and "OK in" in text and "FAILED (auth)" in text and "Fix:" in text and "1 of 2 lane(s) working" in text
    assert check_lanes.run([PRIMARY], factory_for({"primary": Err("bad", 401)}), lambda l: [], out=buf.append) == 1
    assert check_lanes.run([], out=buf.append) == 1 and "No model lane is configured" in buf[-1]


def test_n5_the_command_runs_end_to_end_without_a_traceback():
    out = subprocess.run([sys.executable, "-m", "check_lanes"], cwd=str(ROOT), capture_output=True, text=True, timeout=90,
                         env={**os.environ, "DATABASE_PATH": os.path.join(_TMP, "cl.db"), "OLLAMA_BASE_URL": "http://127.0.0.1:9"})
    assert out.returncode == 1 and "Traceback" not in out.stdout + out.stderr, out.stdout + out.stderr
    assert "Ollama is not answering" in out.stdout and "0 of 1 lane(s) working" in out.stdout


# ---------------- N6: loading and timing ----------------
def test_n6_prepare_builds_each_available_lane_once_and_never_raises():
    built = []
    def builder(lane):
        built.append(lane.slot)
        if lane.slot == "backup": raise llm.MissingPackage("run:  pip install langchain-openai")
        return Graph("x")
    r = llm.LaneRouter(prompt="p", tools=[], lanes=[PRIMARY, BACKUP, LOCAL], graph_builder=builder, clock=Clock(), sleep=lambda s: None)
    out = r.prepare()
    assert [(o["lane"].split(":")[0], o["ok"]) for o in out] == [("primary", True), ("backup", False), ("local", True)]
    assert "pip install langchain-openai" in out[1]["error"]
    r.invoke({"messages": MSGS}); assert built.count("primary") == 1, "an invoke after prepare does not rebuild"
    with env(ANTHROPIC_API_KEY=None):
        assert [o["lane"].split(":")[0] for o in r.prepare()] == ["backup", "local"], "a lane without a key is not built"


def test_n6_failed_attempts_are_timed():
    clock = Clock()
    class SlowFail:
        def invoke(self, inp, config=None): clock.advance(1.5); raise Err("overloaded", 529)
    r = make_router(["primary", "backup"], clock, primary=SlowFail(), backup=Graph("b"))
    r.invoke({"messages": MSGS})
    assert r.last_errors == [(PRIMARY.label, "transient")] * 2 and r.last_timings == [1500, 1500]
    r2 = make_router(["primary"], clock, primary=SlowFail())
    try:
        r2.invoke({"messages": MSGS}); raise AssertionError("fail")
    except llm.LaneUnavailable as e:
        assert e.timings == [1500, 1500]


def test_n6_diagnostics_show_failed_attempts_with_their_time():
    p = "2348800000019"; fresh(p)
    A = agent(FakeAgent(text="ok", last=llm.Use(BACKUP, 1200, 2400, 90, 0), errors=[(PRIMARY.label, "rate_limit")], timings=[900]))
    A.run_agent(p, "hi")
    assert S.get_session(p).last_turn["lane_errors"] == [f"{PRIMARY.label}:rate_limit:900ms"]
    A = agent(FakeAgent(lambda inp: (_ for _ in ()).throw(llm.LaneUnavailable([(PRIMARY.label, "billing"), (BACKUP.label, "other")], [310, 450]))))
    with fake_day("2040-03-01"): A.run_agent(p, "hi again")
    assert S.get_session(p).last_turn["lane_errors"] == [f"{PRIMARY.label}:billing:310ms", f"{BACKUP.label}:other:450ms"]


def test_n6_server_startup_loads_the_lanes_before_customers_arrive():
    from app import main
    seen = []
    old = main.router.prepare
    main.router.prepare = lambda: seen.append(1) or [{"lane": "primary:x", "ok": True, "ms": 1800, "error": ""},
                                                        {"lane": "backup:y", "ok": False, "ms": 5, "error": "MissingPackage: pip install"}]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf): asyncio.run(main.startup())
    finally:
        main.router.prepare = old
    assert seen == [1] and "loaded primary:x in 1800ms" in buf.getvalue() and "COULD NOT LOAD backup:y" in buf.getvalue()
    assert "python -m check_lanes" in buf.getvalue()


def test_n6_chat_loads_the_lanes_before_the_first_message():
    out = subprocess.run([sys.executable, "-m", "chat", "--quiet"], cwd=str(ROOT), input="quit\n", capture_output=True, text=True, timeout=90,
                         env={**os.environ, "DATABASE_PATH": os.path.join(_TMP, "chat2.db")})
    assert out.returncode == 0 and ("loaded local:ollama" in out.stdout or "COULD NOT LOAD" in out.stdout), out.stdout
    assert "python -m check_lanes" in out.stdout


# ---------------- N7: docs and settings ----------------
def test_n7_settings_and_docs_no_longer_suggest_the_retired_model():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "#LLM_BACKUP=groq:openai/gpt-oss-120b" in env_example and "#LLM_BACKUP=groq:llama-3.3" not in env_example
    assert "python -m check_lanes --models" in env_example
    assert "gpt-oss-120b" in (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    doc = (ROOT / "CUSTOMIZE.md").read_text(encoding="utf-8")
    assert "python -m check_lanes" in doc and "Free tiers are small" in doc and "Model names change" in doc


def test_n7_the_prompt_tells_the_model_to_use_only_what_the_customer_typed():
    from app import agent as A
    assert "only with the place the customer actually typed" in A.SYSTEM_PROMPT


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            import traceback; failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
