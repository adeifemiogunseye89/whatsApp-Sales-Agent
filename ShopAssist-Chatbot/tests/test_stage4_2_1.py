"""Stage 4.2.1 verification: fixes found by running Stage 4.2 on the owner's own machine.

Evidence
  * Stage 4.2 showed 3 failures there and Stage 4.1 would have shown 7: the tests read the owner's
    private .env (a free price 0,0, a real Groq lane, no local lane) instead of their own settings
  * a customer received the literal text "[no reply]" when the model returned an empty message
  * the model writes order IDs with a look-alike hyphen (U+2011). A lookup with that hyphen finds
    nothing, and the check that catches INVENTED order IDs looks for a plain hyphen, so an invented
    ID written with the look-alike hyphen slipped past it
  * RESERVATION_HOURS=1.5 stopped the app with a bare ValueError; "1 hours" read badly
Run:  python tests\test_stage4_2_1.py      (no network, no Ollama, no keys)

NEEDS
  N1  tests are isolated from the developer's .env; a bad number in .env explains itself; default hold is 2 h
  N2  an empty model reply is retried and never shown to a customer
  N3  order IDs with look-alike hyphens or spaces work everywhere; the invented-ID check cannot be bypassed
  N4  the hold is worded correctly ("1 hour", "2 hours")
"""
import contextlib, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SHOPASSIST_NO_DOTENV"] = "1"   # tests never read your private .env
_TMP = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_shop421.db")
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
for k in ("LLM_PRIMARY", "LLM_BACKUP", "LLM_LOCAL", "LLM_PRICE_PRIMARY", "LLM_PRICE_BACKUP", "RESERVATION_HOURS"):
    os.environ.pop(k, None)

from langchain_core.messages import AIMessage      # noqa: E402

from app import branches, config, db, llm, orders, textfmt, tools   # noqa: E402
from app import session as S                                        # noqa: E402

db.init_db()
LOOK = "\u2011"          # non-breaking hyphen: what the model wrote in the owner's transcript


class FakeAgent:
    """Replies from a list, one per model call; a reply may also be an exception to raise."""
    def __init__(self, replies):
        self.replies, self.inputs, self.last, self.last_errors, self.last_timings = list(replies), [], None, [], []
    def invoke(self, inp):
        self.inputs.append(inp)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception): raise r
        return {"messages": list(inp["messages"]) + [AIMessage(content=r)]}


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
    S.get_session(phone).branch_just_confirmed = False


def make_order(phone, qty=1):
    shopper(phone)
    call(tools.get_price_quote, phone, items=[tools.CartItem(product_id="P-2001", quantity=qty)])
    orders.confirm_pending(S.get_session(phone))
    return db.orders_for_phone(phone, 1)[0]["order_id"]


# ---------------- N1: isolation and clear errors ----------------
def test_n1_the_dotenv_reader_honours_the_isolation_flag():
    env = Path(tempfile.mkdtemp()) / ".env"
    env.write_text("ZZ_STAGE421_SETTING=from-dotenv\n# a comment\nZZ_STAGE421_EMPTY=\n", encoding="utf-8")
    for k in ("ZZ_STAGE421_SETTING", "ZZ_STAGE421_EMPTY"): os.environ.pop(k, None)
    assert os.environ["SHOPASSIST_NO_DOTENV"] == "1"
    config._load_dotenv(env)
    assert "ZZ_STAGE421_SETTING" not in os.environ, "isolated: the file is not read"
    os.environ["SHOPASSIST_NO_DOTENV"] = "0"
    try:
        config._load_dotenv(env)
        assert os.environ["ZZ_STAGE421_SETTING"] == "from-dotenv" and os.environ["ZZ_STAGE421_EMPTY"] == ""
    finally:
        os.environ["SHOPASSIST_NO_DOTENV"] = "1"
        for k in ("ZZ_STAGE421_SETTING", "ZZ_STAGE421_EMPTY"): os.environ.pop(k, None)


def test_n1_every_suite_isolates_itself_before_it_imports_the_app():
    suites = sorted((ROOT / "tests").glob("test_stage*.py"))
    assert len(suites) >= 10
    for f in suites:
        s = f.read_text(encoding="utf-8")
        flag = s.find('os.environ["SHOPASSIST_NO_DOTENV"] = "1"')
        first_app = min(i for i in (s.find("from app"), s.find("import app")) if i >= 0)
        assert 0 <= flag < first_app, f"{f.name} must set SHOPASSIST_NO_DOTENV before importing the app"


def test_n1_a_private_env_file_cannot_change_a_suite():
    work = Path(tempfile.mkdtemp()) / "proj"
    for name in ("app", "tests", "eval"):
        if (ROOT / name).exists(): shutil.copytree(ROOT / name, work / name, ignore=shutil.ignore_patterns("__pycache__", "*.db*"))
    for name in ("chat.py", "check_lanes.py"): shutil.copy(ROOT / name, work / name)
    (work / ".env").write_text("\n".join([
        "GROQ_API_KEY=gsk_fake_key_for_isolation_test", "ANTHROPIC_API_KEY=sk-ant-fake",
        "LLM_PRIMARY=groq:openai/gpt-oss-120b", "LLM_PRICE_PRIMARY=0,0", "LLM_LOCAL=", "LLM_BACKUP=groq:other-model",
        "RESERVATION_HOURS=3", "DAILY_BUDGET_USD=99", "MODEL_TURNS_PER_HOUR=2", "DAILY_RESERVATION_LIMIT=1",
        "LIMITED_MODE_MESSAGE=custom busy text", "MAX_MESSAGE_CHARS=7"]), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "SHOPASSIST_NO_DOTENV"}
    for suite in ("test_stage1.py", "test_stage3_1.py", "test_stage4.py", "test_stage4_1.py"):
        out = subprocess.run([sys.executable, str(work / "tests" / suite)], cwd=str(work), env=env,
                             capture_output=True, text=True, timeout=400)
        last = (out.stdout.strip().splitlines() or ["(no output)"])[-1]
        n, _, rest = last.partition("/")
        assert rest.startswith(n + " passed"), f"{suite} changed with a private .env: {last}\n{out.stdout[-600:]}{out.stderr[-300:]}"


def test_n1_a_bad_number_in_env_explains_itself():
    for var, value, words in [("RESERVATION_HOURS", "1.5", "must be a whole number"), ("DAILY_BUDGET_USD", "abc", "must be a number")]:
        out = subprocess.run([sys.executable, "-c", "import app.config"], cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                             env={**os.environ, var: value, "DATABASE_PATH": os.path.join(_TMP, "cfg.db")})
        assert out.returncode != 0 and words in out.stderr and var in out.stderr and f"'{value}'" in out.stderr, out.stderr
        assert "Traceback" not in out.stderr, "a typo in .env should read like advice, not like a crash"


def test_n1_the_hold_defaults_to_two_hours():
    assert config.RESERVATION_HOURS == 2
    assert "RESERVATION_HOURS=2" in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()


# ---------------- N2: empty model replies ----------------
def test_n2_an_empty_reply_is_retried_and_never_shown():
    p = "2348910000001"; S.clear(p)
    fake = FakeAgent(["", "Hello there! What are you looking for?"]); A = agent(fake)
    assert A.run_agent(p, "hi") == "Hello there! What are you looking for?"
    t = S.get_session(p).last_turn
    assert t["rejections"] == ["empty_reply"] and t["attempts"] == 2
    second = fake.inputs[1]["messages"]
    assert isinstance(second[-1], dict) and "your last reply was empty" in second[-1]["content"]
    assert not any(getattr(m, "type", "") == "ai" and not str(getattr(m, "content", "")).strip() for m in second), \
        "an empty assistant message must not be sent back (some providers reject it)"
    assert not any(m.type == "ai" and not str(m.content).strip() for m in S.get_session(p).messages), "never saved either"


def test_n2_whitespace_only_counts_as_empty():
    p = "2348910000002"; S.clear(p)
    assert agent(FakeAgent(["   \n  ", "Sure."])).run_agent(p, "hi") == "Sure."
    assert S.get_session(p).last_turn["rejections"] == ["empty_reply"]


def test_n2_if_every_attempt_is_empty_the_customer_gets_a_real_message_not_a_placeholder():
    p = "2348910000003"; S.clear(p)
    A = agent(FakeAgent([""])); out = A.run_agent(p, "hi")
    assert "[no reply]" not in out and "couldn't complete that" in out
    t = S.get_session(p).last_turn
    assert t["path"] == "fallback" and t["attempts"] == 4 and t["rejections"] == ["empty_reply"] * 4


def test_n2_after_a_code_confirmed_branch_an_empty_reply_still_asks_what_to_order():
    p = "2348910000004"; S.clear(p)
    call(tools.set_pickup_location, p, place="Lekki"); S.get_session(p).take_outbox()
    A = agent(FakeAgent([""])); out = A.run_agent(p, "yes")
    assert out.startswith("Pickup branch confirmed: *Lekki Branch*") and "What would you like to order?" in out
    assert S.get_session(p).last_turn["attempts"] == 1, "the branch step already spoke: no retry"


def test_n2_the_placeholder_text_is_gone_from_the_code():
    assert "[no reply]" not in (ROOT / "app" / "agent.py").read_text(encoding="utf-8")


# ---------------- N3: look-alike hyphens in order IDs ----------------
def test_n3_order_ids_are_normalised():
    want = "ORD-1B710288"
    for raw in ["ORD-1B710288", "ord-1b710288", "ORD" + LOOK + "1B710288", "ORD\u20131B710288", "ORD\u22121B710288",
                "ORD 1B710288", "ORD_1B710288", "  ord" + LOOK + "1b710288 "]:
        assert textfmt.normalize_order_id(raw) == want, repr(raw)
    assert textfmt.normalize_order_id("") == "" and textfmt.normalize_order_id(None) == ""
    assert textfmt.normalize_order_id("P-2001") == "P-2001"


def test_n3_only_the_hyphen_right_after_ord_is_fixed():
    assert textfmt.fix_order_dashes("*ORD" + LOOK + "1B710288* \u2013 *RESERVED*") == "*ORD-1B710288* \u2013 *RESERVED*"
    assert textfmt.fix_order_dashes("a pre" + LOOK + "order") == "a pre" + LOOK + "order"
    assert textfmt.fix_order_dashes("WORD" + LOOK + "BANK") == "WORD" + LOOK + "BANK", "ORD inside another word is left alone"


def test_n3_every_order_tool_understands_look_alike_ids():
    p = "2348910000005"; oid = make_order(p)
    for raw in [oid, oid.replace("-", LOOK), oid.replace("-", "\u2013"), oid.replace("-", " "), oid.lower()]:
        assert oid in call(tools.track_order, p, order_id=raw), repr(raw)
        s = S.get_session(p); s.pending_action = None
        call(tools.cancel_order, p, order_id=raw)
        assert S.get_session(p).pending_action["order_id"] == oid and S.get_session(p).pending_action["type"] == "cancel", repr(raw)
        S.get_session(p).pending_action = None; S.get_session(p).take_outbox()
        call(tools.amend_order, p, items=[tools.CartItem(product_id="P-2001", quantity=2)], order_id=raw)
        assert S.get_session(p).pending_action["order_id"] == oid and S.get_session(p).pending_action["type"] == "amend", repr(raw)
        S.get_session(p).pending_action = None; S.get_session(p).take_outbox()


def test_n3_an_invented_order_id_cannot_slip_past_the_check_with_a_look_alike_hyphen():
    p = "2348910000006"; S.clear(p)         # this phone has no orders at all
    A = agent(FakeAgent(["Good news: ORD" + LOOK + "ZZZZZZZZ is on its way to you.", "Which order do you mean?"]))
    out = A.run_agent(p, "any news?")
    assert "ZZZZZZZZ" not in out and out == "Which order do you mean?"
    assert S.get_session(p).last_turn["rejections"] == ["order_mismatch"]


def test_n3_a_real_order_id_reaches_the_customer_with_a_plain_hyphen():
    p = "2348910000007"; oid = make_order(p)
    out = agent(FakeAgent(["Here is your order: " + oid.replace("-", LOOK) + " at Lekki."])).run_agent(p, "what is my order")
    assert oid in out and LOOK not in out


def test_n3_limited_mode_finds_an_order_named_with_a_look_alike_hyphen_or_a_space():
    p = "2348910000008"; oid = make_order(p)
    A = agent(FakeAgent([llm.LaneUnavailable([])]))
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        for raw in [oid.replace("-", LOOK), oid.replace("-", " "), oid.lower()]:
            out = A.run_agent(p, f"where is {raw} please")
            assert out.startswith("Here's what I can see:") and oid in out, repr(raw)
        assert A.run_agent(p, "where is my order please") == A.LIMITED_MODE_MESSAGE, "the word 'order' alone is not an ID"


# ---------------- N4: the hold, worded correctly ----------------
def test_n4_hours_are_worded_correctly():
    assert textfmt.hours_phrase(1) == "1 hour" and textfmt.hours_phrase(2) == "2 hours" and textfmt.hours_phrase(5) == "5 hours"


def test_n4_the_quote_states_the_hold_in_correct_english():
    p = "2348910000009"; old = orders.RESERVATION_HOURS
    try:
        for hours, phrase in ((1, "(held 1 hour)"), (2, "(held 2 hours)")):
            orders.RESERVATION_HOURS = hours
            shopper(p); call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
            assert phrase in S.get_session(p).take_outbox(), hours
    finally:
        orders.RESERVATION_HOURS = old


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
