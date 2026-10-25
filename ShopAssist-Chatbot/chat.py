"""Live chat with your bot — the interactive version of the eval test.

Run from the project root:   python -m chat
Type your message and press Enter. The bot replies.
Type  quit  (or q) to exit,  reset  to start a fresh conversation.
"""
from app.agent import run_agent
from app.config import OLLAMA_MODEL
from app.db import init_db
from app.session import clear, get_session

PHONE = "live-user"   # pretend phone number for your test session


def main():
    init_db()   # create the notebook (tables + your seed data) if missing
    print(f"ShopAssist is online (model: {OLLAMA_MODEL}). "
          "Type 'quit' to exit, 'reset' to start over.\n")
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
        st = get_session(PHONE)   # dev-only: the truth, next to what the model said
        print(f"      [state: branch={st.branch_id} proposed={st.proposed_branch_id} "
              f"quote={st.pending_quote['id'] if st.pending_quote else None} "
              f"handoff={st.handoff_active}]\n")


if __name__ == "__main__":
    main()
