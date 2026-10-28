"""Stage 2.5.1 verification: no stale yes/no proposal for the confirmed branch.

Found in a real chat: the state line read  branch=BR-LKI proposed=BR-LKI  after
every reply, so the model was told a yes/no was pending on a branch the customer
had already confirmed.   Run:  python tests/test_stage2_5_1.py

NEEDS
  N1  proposing the already-confirmed branch is refused at the state level
  N2  the tool says "already confirmed" instead of asking yes/no again
  N3  an already-saved stale proposal is repaired when the session loads
  N4  switching to a DIFFERENT branch still works end to end
"""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test_shop251.db")

from app import db, tools                      # noqa: E402
from app import session as S                   # noqa: E402

db.init_db()


def call(tool_fn, phone, **args):
    tools.set_context(phone)
    return tool_fn.invoke(args)


def confirm_branch(phone, accepted=True):
    """Stage 3.1: the SYSTEM (not a tool) confirms or rejects a proposed branch."""
    from app import branches
    s = S.get_session(phone)
    return branches.confirm_proposal(s) if accepted else branches.reject_proposal(s)


def confirmed(phone, place="Lekki"):
    S.clear(phone)
    call(tools.set_pickup_location, phone, place=place)
    confirm_branch(phone)


def test_n1_cannot_propose_the_confirmed_branch():
    p = "2348300000001"; confirmed(p)
    S.get_session(p).propose_branch("BR-LKI")
    assert S.get_session(p).proposed_branch_id is None


def test_n2_tool_says_already_confirmed_and_asks_nothing():
    p = "2348300000002"; confirmed(p)
    out = call(tools.set_pickup_location, p, place="Lekki")
    assert "already the confirmed pickup branch" in out and "(yes/no)" not in out
    st = S.get_session(p)
    assert st.proposed_branch_id is None and st.branch_id == "BR-LKI"
    assert "waiting for" not in st.state_message().content


def test_n3_stale_saved_proposal_is_repaired_on_load():
    p = "2348300000003"; confirmed(p)
    s = S.get_session(p); s.proposed_branch_id = "BR-LKI"; s.save()    # simulate old bad data
    S._cache.clear()                                                    # restart
    assert S.get_session(p).proposed_branch_id is None


def test_n4_switching_to_another_branch_still_works():
    p = "2348300000004"; confirmed(p, "Lekki")
    call(tools.set_pickup_location, p, place="Ikeja")
    assert "Your pickup branch would be *Ikeja Branch*" in S.get_session(p).take_outbox()
    assert S.get_session(p).proposed_branch_id == "BR-IKJ"
    assert "waiting for the customer's yes/no: Ikeja Branch" in S.get_session(p).state_message().content
    confirm_branch(p)
    st = S.get_session(p)
    assert st.branch_id == "BR-IKJ" and st.proposed_branch_id is None


def test_n4_declining_a_switch_keeps_the_old_branch():
    p = "2348300000005"; confirmed(p, "Lekki")
    call(tools.set_pickup_location, p, place="Wuse")
    confirm_branch(p, False)
    st = S.get_session(p)
    assert st.branch_id == "BR-LKI" and st.proposed_branch_id is None


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try: fn(); print(f"PASS  {name}")
        except Exception as e: failed += 1; print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"); sys.exit(1 if failed else 0)
