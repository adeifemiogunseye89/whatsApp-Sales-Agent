"""Stage 2.5 verification: the MODEL can see the system's state.

Why this stage exists: a real chat with qwen2.5:14b showed the model
 (a) asking for the city again although a branch was already locked,
 (b) saying "we need to confirm the branch first" right after the customer said yes,
 (c) asking "Is this the right branch?" without naming the branch.
All three come from the model being blind to code-owned state, and from tool
messages it was free to paraphrase. Run:   python tests/test_stage2_5.py

NEEDS:
  N1  every model call carries a state note, placed right before the customer's message
  N2  the note is truthful: NONE / confirmed branch / branch awaiting yes-no
  N3  the note is never saved into chat history (it is rebuilt each turn)
  N4  the branch proposal text names the branch + address; confirm text says to carry on
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shop25.db")

from langchain_core.messages import AIMessage              # noqa: E402

from app import db, tools                                   # noqa: E402
from app import session as S                                # noqa: E402

db.init_db()


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


class FakeAgent:
    def __init__(self):
        self.calls = []
    def invoke(self, inp):
        self.calls.append(list(inp["messages"]))
        return {"messages": list(inp["messages"]) + [AIMessage(content="ok")]}


def _agent():
    from app import agent as A
    A.agent = FakeAgent()
    return A


# ---- N2: truthful note ----
def test_n2_note_says_none_without_branch():
    p = "2348200000001"; S.clear(p)
    txt = S.get_session(p).state_message().content
    assert "Confirmed pickup branch: NONE" in txt and "NEVER ask" not in txt


def test_n2_note_names_confirmed_branch_and_forbids_reasking():
    p = "2348200000002"; S.clear(p)
    call(tools.set_pickup_location, p, place="Lekki")
    call(tools.confirm_pickup_branch, p, accepted=True)
    txt = S.get_session(p).state_message().content
    assert "Lekki Branch" in txt and "Lekki Phase 1" in txt
    assert "NEVER ask for the city or area again" in txt and "NONE" not in txt


def test_n2_note_shows_branch_waiting_for_yes_no():
    p = "2348200000003"; S.clear(p)
    call(tools.set_pickup_location, p, place="Wuse")
    txt = S.get_session(p).state_message().content
    assert "waiting for the customer's yes/no: Wuse Branch" in txt
    assert "call confirm_pickup_branch" in txt


# ---- N1: placement ----
def test_n1_model_receives_note_right_before_customer_message():
    A = _agent(); p = "2348200000004"; S.clear(p)
    A.run_agent(p, "hello")                       # earlier turns, so position matters
    A.run_agent(p, "any rice?")
    A.run_agent(p, "how much is basmati rice?")
    sent = A.agent.calls[-1]
    assert len(sent) >= 5, "history should precede the note"
    assert sent[-1].type == "human" and sent[-1].content == "how much is basmati rice?"
    assert sent[-2].type == "system" and sent[-2].name == S.STATE_MSG_NAME


def test_n1_note_reflects_state_on_the_next_turn():
    A = _agent(); p = "2348200000005"; S.clear(p)
    A.run_agent(p, "hi")
    assert "NONE" in A.agent.calls[-1][-2].content
    call(tools.set_pickup_location, p, place="Ikeja")
    call(tools.confirm_pickup_branch, p, accepted=True)
    A.run_agent(p, "how much is rice?")
    assert "Ikeja Branch" in A.agent.calls[-1][-2].content


# ---- N3: never persisted ----
def test_n3_note_is_not_saved_to_history():
    A = _agent(); p = "2348200000006"; S.clear(p)
    for i in range(3):
        A.run_agent(p, f"msg {i}")
    assert all(m.type != "system" for m in S.get_session(p).messages)
    S._cache.clear()                                    # restart
    assert all(m.type != "system" for m in S.get_session(p).messages)
    assert [m.type for m in A.agent.calls[-1]].count("system") == 1, "exactly one note per call"


# ---- N4: tool wording ----
def test_n4_proposal_names_branch_and_address_and_asks_yes_no():
    out = call(tools.set_pickup_location, "2348200000007", place="Lekki")
    assert out.startswith("Proposed branch")                 # old contract kept
    assert 'Your pickup branch would be *Lekki Branch* — Demo address, Lekki Phase 1' in out
    assert "(yes/no)" in out


def test_n4_confirm_tells_model_to_carry_on():
    p = "2348200000008"; S.clear(p)
    call(tools.set_pickup_location, p, place="Lekki")
    out = call(tools.confirm_pickup_branch, p, accepted=True)
    assert "locked" in out and "carry on" in out and "do not ask the location question again" in out


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
