"""Live chat with your bot — the interactive version of the eval test.

Run from the project root:   python -m chat            (add --quiet to hide the developer lines)
Type your message and press Enter. The bot replies.
Type  quit  (or q) to exit,  reset  to start a fresh conversation.
"""
import sys

from app.agent import router, run_agent
from app.db import init_db, open_orders_for_phone
from app.session import clear, get_session

PHONE = "live-user"   # pretend phone number for your test session
QUIET = "--quiet" in sys.argv   # hide the [state] / [turn] developer lines


def main():
    init_db()   # create the notebook (tables + your seed data) if missing
    print("ShopAssist is online. Model lanes:")
    for line in router.describe():
        print(f"  - {line}")
    for r in router.prepare():                 # load now, so the first message is not slow
        print(f"  loaded {r['lane']} in {r['ms'] / 1000:.1f}s" if r["ok"] else
              f"  COULD NOT LOAD {r['lane']}: {r['error']}  (run: python -m check_lanes)")
    print("Type 'quit' to exit, 'reset' to start over. (Problems with a lane? Run: python -m check_lanes)\n")
    while True:
        try:
            user_text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not user_text:
            continue
        if user_text.lower() in ("quit", "q", "exit"):
            print("Bye!")
            break
        if user_text.lower() == "reset":
            clear(PHONE)
            print("[conversation wiped clean]")
            continue
        try:
            reply = run_agent(PHONE, user_text)
        except Exception as e:
            print(f"Bot: [hiccup — {e}] Try rephrasing, or type 'reset'.\n")
            continue
        if reply is None:
            print("Bot: [a human agent has this chat - bot is silent]\n")
            continue
        if not reply.strip():
            print("Bot: [thinking came back empty — please repeat that]\n")
            continue
        print(f"Bot: {reply}\n")
        if QUIET:
            continue
        st = get_session(PHONE)   # dev-only: the truth, next to what the model said
        print(f"      [state: branch={st.branch_id} proposed={st.proposed_branch_id} "
              f"quote={st.pending_quote['id'] if st.pending_quote else None} "
              f"action={st.pending_action['type'] if st.pending_action else None} "
              f"open={len(open_orders_for_phone(PHONE))} handoff={st.handoff_active}]")
        t = st.last_turn   # dev-only: which brain answered, how fast, at what cost?
        if t:
            cost = (t.get("cost_micro_usd") or 0) / 1_000_000
            print(f"      [turn: path={t.get('path')} lane={t.get('lane') or '-'} "
                  f"attempts={t.get('attempts')} rejected={','.join(t.get('rejections', [])) or '-'} "
                  f"tools={','.join(t.get('tools', [])) or '-'} model={t.get('model_ms', 0)}ms "
                  f"total={t.get('ms')}ms cost=${cost:.5f}]\n")


if __name__ == "__main__":
    main()
