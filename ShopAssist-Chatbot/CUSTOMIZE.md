# Where YOU Add Your Own Input

Seven places, in the order you'll touch them. Current through Stage 4.

## 1. Environment / credentials: `.env` (copy from `.env.example`)
Anything you set in `.env` overrides the default in `app/config.py`.

| Variable | What it does | Default |
|---|---|---|
| `OLLAMA_MODEL`, `OLLAMA_BASE_URL` | The local model. `ollama pull <model>` first. | see `.env.example` |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Live WhatsApp only. Empty = dev mode (replies print to the console). | empty |
| `WHATSAPP_VERIFY_TOKEN` | Any secret you choose; must match the Meta dashboard. | |
| `CURRENCY` | Symbol shown in messages. | `₦` |
| `TAX_RATE`, `DELIVERY_FEE_KOBO` | Business rules. All money is whole KOBO (100 kobo = ₦1). | `0`, `0` |
| `QUOTE_TTL_MINUTES` | How long a customer has to reply YES to a quote. | `10` |
| `RESERVATION_HOURS` | How long a reservation holds stock. | `5` |
| `GRACE_MINUTES` | Extra time to collect after the hold ends; stock is released when grace ends. | `30` |
| `DAILY_RESERVATION_LIMIT` | Reservations per phone in any rolling 24 hours (cancelled and expired ones count). | `3` |
| `SWEEP_MINUTES` | How often expired holds are released while the server runs. | `5` |
| `SESSION_TTL_HOURS`, `HISTORY_CAP` | How long an idle chat is remembered, and how many messages are kept. | `24`, `20` |
| `HANDOFF_HOURS`, `HUMAN_ESCALATION_WEBHOOK` | How long the bot stays silent after handing a chat to a human, and where the alert is posted. | `4`, empty |
| `LOG_DIR` | Per-turn diagnostics (`turns.jsonl`: metadata only, never customer text). | `logs` |

## 2. Branches, products and per-branch prices: `app/db.py`
Three seed lists near the top of the file. Seeding only runs on an EMPTY database,
so after changing them delete `shop.db` (development only).

```python
SEED_BRANCHES  = [("BR-LKI", "Lekki Branch", "Lagos", "Demo address, Lekki Phase 1")]
SEED_PRODUCTS  = [("P-1001", "Royal Basmati Rice 5kg", "Premium aged basmati rice.", "Rice")]
SEED_INVENTORY = [("BR-LKI", "P-1001", 1450000, 12)]   # branch, product, price in KOBO, stock
```

Rules that avoid errors:
- `SEED_PRODUCTS` rows have exactly 4 fields: id, name, description, category. There is NO price or stock in them.
- Price and stock live per branch in `SEED_INVENTORY` (4 fields). Price is in kobo: ₦14,500 is `1450000`.
- Every `branch_id` and `product_id` used in `SEED_INVENTORY` must exist in the other two lists.
- No row in `SEED_INVENTORY` for a branch and product means that branch does not stock it.
- Commas, quotes and brackets must be exact: one typo is a `SyntaxError` when the app starts.
- This is fine for a handful of products. For a real catalogue (the 150-product demo files) use the CSV importer planned for Stage 5 instead of editing code.

## 3. Agent persona and rules: `app/agent.py` -> `SYSTEM_PROMPT`
Change the store name, tone and wording. Keep the STRICT RULES section: it is what keeps the model honest.
There is deliberately no example conversation in the prompt (small models copy examples verbatim).

## 4. Tool behaviours: `app/tools.py`
- Tools are listed in `ALL_TOOLS` (bottom of file).
- There is deliberately no `create_order` or `confirm_pickup_branch` tool. Orders (`app/orders.py`) and branches (`app/branches.py`) are confirmed by code after the customer replies YES.
- `get_price_quote`, `amend_order` and `cancel_order` only PREPARE a request; the system shows it and acts after YES.
- Payment is not handled by the bot: customers pay at the shop counter.

## 5. Scaling swaps (only when volume demands): marked `SWAP` in the code
`app/session.py` (Redis), `app/db.py` (Postgres), `app/main.py` (queue), `app/agent.py` (hosted model).
Run ONE server process for now: sessions are cached in memory.

## 6. Tests: `tests/`
Run from the `ShopAssist-Chatbot` folder. None of them needs Ollama or WhatsApp.

| Command | Expected last line |
|---|---|
| `python tests/test_stage1.py` | `15/15 passed` |
| `python tests/test_stage2.py` | `17/17 passed` |
| `python tests/test_stage2_5.py` | `8/8 passed` |
| `python tests/test_stage2_5_1.py` | `5/5 passed` |
| `python tests/test_stage3.py` | `22/22 passed` |
| `python tests/test_stage3_1.py` | `19/19 passed` |
| `python tests/test_stage4.py` | `35/35 passed` |

## 7. Eval suite: `eval/test_queries.py`
Replace `QUERIES` with your real customer phrases; add assertions on tool calls and response text to turn it into a regression suite.