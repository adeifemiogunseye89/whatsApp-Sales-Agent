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
| `RESERVATION_HOURS` | How long a reservation holds stock, in WHOLE hours (a decimal such as 1.5 stops the app from starting). The stock is actually released this long plus `GRACE_MINUTES` after the reservation. | `2` |
| `GRACE_MINUTES` | Extra time to collect after the hold ends; stock is released when grace ends. | `30` |
| `DAILY_RESERVATION_LIMIT` | Reservations per phone in any rolling 24 hours (cancelled and expired ones count). | `3` |
| `SWEEP_MINUTES` | How often expired holds are released while the server runs. | `5` |
| `SESSION_TTL_HOURS`, `HISTORY_CAP` | How long an idle chat is remembered, and how many messages are kept. | `24`, `20` |
| `HANDOFF_HOURS`, `HUMAN_ESCALATION_WEBHOOK` | How long the bot stays silent after handing a chat to a human, and where the alert is posted. | `4`, empty |
| `LOG_DIR` | Per-turn diagnostics (`turns.jsonl`: metadata only, never customer text). | `logs` |
| `LLM_PRIMARY`, `LLM_BACKUP`, `LLM_LOCAL` | The bot's brain, tried in this order. Each is `provider:model`. See section 8. | local only |
| `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, ... | Keys for the providers you use. Never commit a real key. | empty |
| `DAILY_BUDGET_USD`, `BUDGET_ALERT_PERCENT` | Paid lanes pause for the day at the budget (`0` = no cap); alert at the percentage. | `0.50`, `80` |
| `MODEL_TURNS_PER_HOUR`, `MAX_MESSAGE_CHARS` | Per phone: model turns per hour, and the longest message the model reads. | `30`, `600` |
| `LLM_TIMEOUT_HOSTED`, `LLM_TIMEOUT_LOCAL`, `LLM_MAX_OUTPUT_TOKENS` | Seconds to wait for a hosted / local call; longest reply. | `20`, `300`, `300` |
| `OLLAMA_NUM_CTX`, `OLLAMA_KEEP_ALIVE`, `LLM_WARM_UP` | Local model memory window, how long it stays loaded, and whether to load it at server start. | `6144`, `1h`, off |
| `LIMITED_MODE_MESSAGE` | What customers see when no brain is available. | We're a bit busy; the team will reply shortly. |

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
| `python tests/test_stage4_1.py` | `43/43 passed` |
| `python tests/test_stage4_2.py` | `42/42 passed` |
| `python tests/test_stage4_2_1.py` | `18/18 passed` (the suites ignore your private `.env` on purpose) |

## 7. Eval suite: `eval/test_queries.py`
Replace `QUERIES` with your real customer phrases; add assertions on tool calls and response text to turn it into a regression suite.

## 8. Choosing the brain (Stage 4.1)
The model is a setting. Lanes are tried in order: primary -> backup -> local.

Quick start with a hosted model (about $5 prepaid lasts thousands of turns):
1. Create an account with a provider and load a small prepaid amount. Prepaid credit means the account can never spend more than you loaded.
2. Put the key in `.env`, for example `ANTHROPIC_API_KEY=...` (never commit it).
3. Install the matching package once: `pip install langchain-anthropic` (or `langchain-openai` for groq, gemini, openrouter, mistral, cerebras, openai).
4. Set the lane, for example `LLM_PRIMARY=anthropic:claude-haiku-5-5`. Keep `LLM_LOCAL` as the slow last resort, or set it empty.
5. Run `python -m check_lanes`. It makes one tiny call per lane and tells you in plain English what to fix (no credit, retired model name, rate limit, wrong key). Add `--models` to list the model names your key can use.
6. Start `python -m chat`. It loads each lane first (so your first message is not slow), then prints each lane with its price and whether its key is found. Each reply shows which lane answered, the model time and the cost.

Rules to know:
- **Price.** `claude-haiku-5-5` is built in. For any other hosted model set `LLM_PRICE_PRIMARY=input,output` (USD per million tokens, `0,0` for a free tier). An unknown price is assumed to be expensive on purpose, so spend is never hidden.
- **Budget.** At `DAILY_BUDGET_USD` paid lanes pause until tomorrow (Lagos time); the local lane still works. You get an alert once a day at `BUDGET_ALERT_PERCENT` and at the cap (console, plus `HUMAN_ESCALATION_WEBHOOK` if set).
- **Free tiers are small.** For example Groq's free tier allowed 8,000 tokens a minute; one of our model calls is about 2,400 tokens, so only 1 or 2 customer messages a minute fit. Use a free tier to build and test, a paid or higher-limit plan for the demo.
- **Rate limits.** If a provider asks for a short wait (6 seconds or less) the bot waits and retries once inside the same turn. A longer limit rests that lane (never more than 2 minutes) and the next lane is tried. Credit problems and bad keys pause a lane for 30 minutes.
- **Model names change.** Providers retire models. If a lane says the model was not found, run `python -m check_lanes --models` and use a name from that list.
- **Failures.** A slow hosted lane is retried once, then the next lane is used. A bad key or no credit pauses that lane for 30 minutes. If every lane fails, customers get `LIMITED_MODE_MESSAGE`; YES/NO answers, quotes and naming an order ID still work.
- **Abuse.** `MODEL_TURNS_PER_HOUR` limits one phone; longer messages are cut to `MAX_MESSAGE_CHARS`. This stops one number from draining the budget. It does not stop someone with many numbers: the daily budget is the backstop.
- **Privacy.** Hosted lanes send the chat text (no phone numbers) to the provider. Use free tiers for demos with fake data only, and read each provider's data terms before real customers.
- **Local tuning.** If the local lane is first, `LLM_WARM_UP=1` loads the model at server start. `OLLAMA_NUM_CTX` caps the memory window (a smaller window uses less memory).
- `python -m chat --quiet` hides the developer lines.
