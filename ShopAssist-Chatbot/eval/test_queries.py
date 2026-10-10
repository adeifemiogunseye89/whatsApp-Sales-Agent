"""Offline eval harness: run canned queries through the agent with the dev
WhatsApp stub (no Meta needed). Extend QUERIES into a golden dataset and add
assertions on tool calls / response content to make it a real regression suite.

Run from project root:  python eval/test_queries.py
"""
from app.agent import run_agent
from app.db import init_db, get_conn
from app.session import clear

QUERIES = [
    "do you have rice in stock?",
    "tell me about product P-1001",
    "i want 2 of the royal basmati and 1 milk",
    "how much for 1 x P-3001 and 5 x P-2001?",
    "yes go ahead, place the order",   # should be refused (no confirmation context)
    "where is my order?",
    "i want a refund for my last order",
    "do you sell laptops?",            # honest 'no data' case
]


def main():
    init_db()
    clear("test-user")
    for q in QUERIES:
        print(f"\n>>> {q}")
        reply = run_agent("test-user", q)
        print(f"<<< {reply}")
    print("\n--- orders in DB after run ---")
    for r in get_conn().execute("SELECT order_id, customer_phone, total FROM orders"):
        print(dict(r))


if __name__ == "__main__":
    main()
