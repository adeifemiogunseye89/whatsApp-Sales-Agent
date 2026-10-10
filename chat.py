
"""Live chat with your bot — the interactive version of the eval test.

Run from the project root:   python -m chat
Type your message and press Enter. The bot replies.
Type  quit  (or q) to exit,  reset  to start a fresh conversation.
"""
from app.agent import run_agent
from app.session import clear

PHONE = "live-user"   # pretend phone number for your test session


def main():
    print("ShopAssist is online. Type 'quit' to exit, 'reset' to start over.\n")
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
        reply = run_agent(PHONE, user_text)
        print(f"Bot: {reply}\n")


if __name__ == "__main__":
    main()
