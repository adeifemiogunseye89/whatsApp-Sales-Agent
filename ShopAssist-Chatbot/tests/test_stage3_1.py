

"""Stage 3.1 verification: code owns the branch step; the model cannot lie about branches.

Found in a live chat (qwen2.5:14b): the bot invented a 'Victoria Island Branch' and
three addresses, said "we've set Victoria Island Branch" while no branch was set,
looped on the same invented text, copied the prompt's example conversation
("(price) (6 in stock)", "Assistant: (call ...)"), and once confirmed a branch on
the customer's behalf.   Run:  python tests/test_stage3_1.py   (no Ollama needed)

NEEDS
  N1  nothing copyable remains in the prompt; the model has no way to confirm a branch
  N2  every branch message the customer reads is written by code from the database
  N3  a plain YES/NO to a branch proposal is answered by code; on YES the model then
      carries on with the original request
  N4  replies naming a non-existent branch, showing unverified branch details, or
      claiming a branch is set (when none is) are rejected and retried
  N5  rejected replies never reach chat history, and the emergency exit shows none of them
"""
import os, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shop31.db")

from langchain_core.messages import AIMessage, ToolMessage   # noqa: E402

from app import branches, db, guards, tools            # noqa: E402
from app import session as S                           # noqa: E402

db.init_db()


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def text_of(m):
    return m["content"] if isinstance(m, dict) else (m.content or "")


class FakeAgent:
    """Stands in for the LLM; `script(inp)` may call real tools and returns the model's text."""
    def __init__(self, script=None, text="ok"):
        self.calls, self.script, self.text = [], script, text
    def invoke(self, inp):
        self.calls.append(list(inp["messages"]))
        out = self.script(inp) if self.script else self.text
        return {"messages": list(inp["messages"]) + [AIMessage(content=out)]}


def agent_mod(fake):
    from app import agent as A
    A.agent = fake
    return A


def fresh(phone):
    S.clear(phone)
    return S.get_session(phone)


def history_text(phone):
    return "\n".join(text_of(m) for m in S.get_session(phone).messages)


def sess(branch_id=None, proposed=None):
    s = S.Session(phone="x"); s.branch_id = branch_id; s.proposed_branch_id = proposed
    return s


# ---------------- N1 ----------------
def test_n1_prompt_has_nothing_to_copy_and_no_branch_confirm_tool():
    from app import agent as A
    p = A.SYSTEM_PROMPT
    for needle in ("EXAMPLE CONVERSATION", "(price)", "Customer:", "Assistant:", "(6 in stock)", "Wuse Road"):
        assert needle not in p, needle
    names = {t.name for t in tools.ALL_TOOLS}
    assert "confirm_pickup_branch" not in names and "confirm_pickup_branch" not in p
    assert set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", p)) <= names
    assert "NEVER say a pickup branch is set" in p


# ---------------- N2 ----------------
def test_n2_branch_texts_are_code_written_from_the_database():
    p = "2348500000001"; fresh(p)
    out = call(tools.set_pickup_location, p, place="Lagos")
    assert out.startswith("BRANCH MESSAGE SHOWN") and "Lekki" not in out and "Ikeja" not in out
    shown = S.get_session(p).take_outbox()
    assert "1. *Ikeja Branch* — Demo address, Allen Avenue, Ikeja" in shown
    assert "2. *Lekki Branch* — Demo address, Lekki Phase 1" in shown
    call(tools.set_pickup_location, p, place="Victoria Island")
    none = S.get_session(p).take_outbox()
    assert "couldn't find a branch for 'Victoria Island'" in none and "Abuja, Lagos" in none


def test_n2_customer_sees_code_text_even_if_model_invents_branches():
    p = "2348500000002"; fresh(p)
    def script(inp):
        call(tools.set_pickup_location, p, place="Lagos")
        return "We have Ikeja (123 Ikeja Road), Lekki (456 Lekki Road) and Victoria Island Branch."
    A = agent_mod(FakeAgent(script))
    out = A.run_agent(p, "branches in Lagos")
    assert "Victoria" not in out and "123 Ikeja Road" not in out and "Demo address, Lekki Phase 1" in out
    assert "Victoria" not in history_text(p), "history records what the customer saw"


def test_n2_single_match_proposal_and_already_confirmed():
    p = "2348500000003"; fresh(p)
    call(tools.set_pickup_location, p, place="Lekki")
    assert S.get_session(p).proposed_branch_id == "BR-LKI"
    assert "Reply *YES* or *NO*" in S.get_session(p).take_outbox()
    branches.confirm_proposal(S.get_session(p))
    out = call(tools.set_pickup_location, p, place="Lekki")
    assert "already the confirmed pickup branch" in out and S.get_session(p).outbox is None


# ---------------- N3 ----------------
def _propose(p, place="Lekki"):
    fresh(p); call(tools.set_pickup_location, p, place=place); S.get_session(p).take_outbox()


def test_n3_yes_locks_the_branch_in_code_and_model_resumes():
    p = "2348500000004"; _propose(p)
    fake = FakeAgent(text="Basmati is *₦14,500* (5 in stock). Want some?")
    A = agent_mod(fake)
    out = A.run_agent(p, "yes")
    assert out.startswith("Pickup branch confirmed: *Lekki Branch* — Demo address, Lekki Phase 1.")
    assert out.endswith("Basmati is *₦14,500* (5 in stock). Want some?")
    st = S.get_session(p)
    assert st.branch_id == "BR-LKI" and st.proposed_branch_id is None
    note = fake.calls[0][-2].content
    assert "JUST confirmed this branch" in note and "Lekki Branch" in note
    assert S.get_session(p).messages[-1].content == out


def test_n3_model_going_quiet_still_gets_a_sensible_reply():
    p = "2348500000005"; _propose(p)
    A = agent_mod(FakeAgent(text=""))
    assert A.run_agent(p, "yes please").endswith("What would you like to order?")


def test_n3_the_model_alone_can_never_lock_a_branch():
    p = "2348500000006"; fresh(p)
    A = agent_mod(FakeAgent(lambda inp: (call(tools.set_pickup_location, p, place="Lekki"), "Locked it!")[1]))
    A.run_agent(p, "price of rice at lekki")
    assert S.get_session(p).branch_id is None and S.get_session(p).proposed_branch_id == "BR-LKI"


def test_n3_no_is_answered_by_code_without_the_model():
    p = "2348500000007"; _propose(p)
    boom = FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("model must not run")))
    A = agent_mod(boom)
    out = A.run_agent(p, "no")
    assert "Which city or area" in out and boom.calls == []
    assert S.get_session(p).branch_id is None and S.get_session(p).proposed_branch_id is None


def test_n3_no_to_a_switch_keeps_the_current_branch():
    p = "2348500000008"; fresh(p)
    call(tools.set_pickup_location, p, place="Lekki"); branches.confirm_proposal(S.get_session(p))
    call(tools.set_pickup_location, p, place="Ikeja"); S.get_session(p).take_outbox()
    A = agent_mod(FakeAgent(lambda inp: (_ for _ in ()).throw(AssertionError("no model"))))
    assert "keep *Lekki Branch*" in A.run_agent(p, "nope")
    assert S.get_session(p).branch_id == "BR-LKI"


def test_n3_unclear_reply_goes_to_the_model_and_keeps_the_proposal():
    p = "2348500000009"; _propose(p)
    fake = FakeAgent(text="Sure - which place?")
    A = agent_mod(fake)
    A.run_agent(p, "yes but in ikeja")
    assert len(fake.calls) == 1 and S.get_session(p).proposed_branch_id == "BR-LKI"
    assert "you cannot confirm a branch" in fake.calls[0][-2].content


def test_n3_proposal_survives_restart_then_yes_works():
    p = "2348500000010"; _propose(p)
    S._cache.clear()
    A = agent_mod(FakeAgent(text="Anything specific?"))
    assert A.run_agent(p, "yes").startswith("Pickup branch confirmed")


# ---------------- N4 ----------------
def test_n4_guard_table():
    none = sess()
    lekki = sess("BR-LKI")
    cases = [
        # (session, reply, tools_called, should_be_flagged)
        (none,  "Sure, we have *Victoria Island Branch* — 789 VI Road", set(), True),
        (none,  "Ikeja Branch: 123 Ikeja Road, Ikeja", set(), True),
        (none,  "Great! We've set Victoria Island Branch as your pickup location.", set(), True),
        (none,  "Great! Your pickup branch is confirmed as Lekki Branch.", set(), True),
        (none,  "Your pickup branch has been locked. What would you like?", set(), True),
        (lekki, "Our Ikeja Branch also has rice.", set(), True),
        (none,  "Which city or area will the pickup happen in?", set(), False),
        (none,  "Is this the right pickup branch?", set(), False),
        (none,  "Please confirm your pickup branch.", set(), False),
        (none,  "Which branch would you like to use?", set(), False),
        (none,  "Is this the right Pickup Branch?", set(), False),
        (lekki, "At the Lekki Branch, Royal Basmati Rice 5kg is *₦14,500*.", set(), False),
        (lekki, "Great Lekki Branch has rice.", set(), False),
        (lekki, "Our *Lekki Branch* is open.", set(), False),
        (sess(proposed="BR-IKJ"), "Ikeja Branch is the one I found.", set(), False),
        (none,  "Ikeja Branch and Lekki Branch are in Lagos.", {"set_pickup_location"}, False),
        (lekki, "Your order is reserved at Lekki Branch.", set(), False),
    ]
    for s, reply, called, flagged in cases:
        got = guards.bad_branch_claim(s, reply, called)
        assert bool(got) == flagged, (reply, got)


def test_n4_branch_names_the_tools_returned_this_turn_are_allowed():
    # Real case: after `reset`, "where is my order" -> "Your recent orders at Lekki Branch ..."
    none = sess()
    reply = "Your recent orders at Lekki Branch are: ORD-1"
    assert guards.bad_branch_claim(none, reply, {"track_order"}) is not None      # no evidence -> flagged
    assert guards.bad_branch_claim(none, reply, {"track_order"}, "- ORD-1: RESERVED at Lekki Branch, total ₦1") is None
    assert guards.bad_branch_claim(none, "Also Ikeja Branch!", {"track_order"}, "RESERVED at Lekki Branch") is not None


def test_n4_tracking_after_reset_is_not_blocked_end_to_end():
    from app import orders
    p = "2348500000016"; fresh(p)
    call(tools.set_pickup_location, p, place="Lekki"); branches.confirm_proposal(S.get_session(p))
    S.get_session(p).take_outbox()
    call(tools.get_price_quote, p, items=[tools.CartItem(product_id="P-2001", quantity=1)])
    orders.confirm_pending(S.get_session(p))
    S.clear(p)                                          # customer typed `reset`: branch forgotten
    class ToolUsingAgent:
        """Like real LangGraph: a tool-call message, the tool's result, then the answer."""
        def __init__(self): self.calls = []
        def invoke(self, inp):
            self.calls.append(list(inp["messages"]))
            out = call(tools.track_order, p)
            return {"messages": list(inp["messages"]) + [
                AIMessage(content="", tool_calls=[{"name": "track_order", "args": {}, "id": "t1"}]),
                ToolMessage(content=out, tool_call_id="t1"),
                AIMessage(content="Your recent orders at Lekki Branch are:\n" + out)]}
    fake = ToolUsingAgent(); A = agent_mod(fake)
    reply = A.run_agent(p, "where is my order")
    assert len(fake.calls) == 1, "must not be rejected and retried"
    assert "ORD-" in reply and "Lekki Branch" in reply


def test_n4_replay_of_the_real_transcript_self_corrects():
    """The model misbehaves exactly like qwen did; the notice sends it to the tool."""
    p = "2348500000011"; fresh(p)
    def script(inp):
        noticed = any("BRANCH CHECK FAILED" in text_of(m) for m in inp["messages"])
        if not noticed:
            return ("Sure, here are the branches in Lagos:\n- Ikeja Branch: 123 Ikeja Road\n"
                    "- Lekki Branch: 456 Lekki Road\n- Victoria Island Branch: 789 VI Road")
        call(tools.set_pickup_location, p, place="Lagos")
        return "Here you go"
    fake = FakeAgent(script)
    A = agent_mod(fake)
    out = A.run_agent(p, "branches in Lagos")
    assert len(fake.calls) == 2, "first attempt rejected, second accepted"
    assert "Victoria" not in out and "456 Lekki Road" not in out
    assert "Demo address, Lekki Phase 1" in out and "Demo address, Allen Avenue, Ikeja" in out


def test_n4_false_set_claim_is_rejected():
    p = "2348500000012"; fresh(p)
    def script(inp):
        if any("BRANCH CHECK FAILED" in text_of(m) for m in inp["messages"]):
            return "Which city or area will the pickup happen in?"
        return "Great! We've set Victoria Island Branch as your pickup location."
    A = agent_mod(FakeAgent(script))
    out = A.run_agent(p, "yes")
    assert out == "Which city or area will the pickup happen in?" and S.get_session(p).branch_id is None


# ---------------- N5 ----------------
def test_n5_rejected_attempts_never_reach_history():
    p = "2348500000013"; fresh(p)
    def script(inp):
        if any("BRANCH CHECK FAILED" in text_of(m) for m in inp["messages"]):
            return "Which city or area will the pickup happen in?"
        return "We have Victoria Island Branch at 789 VI Road."
    A = agent_mod(FakeAgent(script))
    A.run_agent(p, "which branches do you have")
    h = history_text(p)
    assert "Victoria" not in h and "SYSTEM NOTICE" not in h and "BRANCH CHECK" not in h
    S._cache.clear()
    assert "Victoria" not in history_text(p)


def test_n5_emergency_exit_shows_and_saves_none_of_the_rejected_text():
    p = "2348500000014"; fresh(p)
    fake = FakeAgent(text="Yes! Victoria Island Branch, 789 VI Road, is ready.")
    A = agent_mod(fake)
    out = A.run_agent(p, "branches?")
    assert len(fake.calls) == 4
    assert "Victoria" not in out and "human agent" in out and "connect you" not in out
    assert "Victoria" not in history_text(p)


def test_n5_emergency_exit_after_a_yes_keeps_the_confirmation():
    p = "2348500000015"; _propose(p)
    A = agent_mod(FakeAgent(text="Victoria Island Branch has it!"))
    out = A.run_agent(p, "yes")
    assert out.startswith("Pickup branch confirmed: *Lekki Branch*") and out.endswith("What would you like to order?")
    assert "Victoria" not in out and S.get_session(p).branch_id == "BR-LKI"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e:
            failed += 1; print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
