# Where YOU Add Your Own Input

Six injection points, in the order you'll touch them:

## 1. Environment / credentials — `.env`  (copy from `.env.example`)
| Variable | When needed |
|---|---|
| `OLLAMA_MODEL`, `OLLAMA_BASE_URL` | Always. `ollama pull llama3.1:8b` first. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Only for live WhatsApp. Empty = dev mode (replies print to console). |
| `WHATSAPP_VERIFY_TOKEN` | Any secret you choose; must match Meta dashboard. |
| `TAX_RATE`, `DELIVERY_FEE`, `CURRENCY` | Your business rules. |

## 2. Your product catalog — `app/db.py` -> `SEED_PRODUCTS`
Replace the 6 demo rows with your real products:
`("P-1001", "Name", "Description", unit_price, stock_qty, "Category")`
Delete `shop.db` (or set a fresh `DATABASE_PATH`) after changing, or load
from your real DB by replacing `init_db()` with an import script.

## 3. Agent persona + rules — `app/agent.py` -> `SYSTEM_PROMPT`
Change the store name, tone, currency notes, and the STRICT RULES section.
Keep rules 1-5; they are what keep the model honest.

## 4. Tool behaviors — `app/tools.py`
- Add/remove tools in `ALL_TOOLS` (bottom of file).
- Wire `escalate_to_human` to your CRM/Slack via `HUMAN_ESCALATION_WEBHOOK` in `.env`.
- Add payment logic inside `create_order` after `db.create_order_db(...)` returns.

## 5. Scaling swaps (only when volume demands) — marked `SWAP` in code
`app/session.py` (Redis), `app/db.py` (Postgres), `app/main.py` (queue), `app/agent.py` (LiteLLM for cloud models).

## 6. Eval suite — `eval/test_queries.py`
Replace `QUERIES` with your real customer phrases; add assertions on tool
calls and response text to turn it into a regression suite.
