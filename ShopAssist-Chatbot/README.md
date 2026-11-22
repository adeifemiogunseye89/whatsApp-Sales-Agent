# ShopAssist: WhatsApp sales and reservation bot (demo stage)

A WhatsApp bot that lets customers of a multi-branch shop check prices and stock, get a quote, and
**reserve items for pickup** (pay at the counter). Built as a demo for malls and supermarkets.

**Design rule:** code owns every high-stakes step (branch choice, quotes, reservations, expiry, limits,
money). The AI model only understands the customer's message, picks a lookup, and phrases answers.
Anything the model says about branches or orders is checked against the database.

> New to the project, or starting a new chat session? Read **[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)**
> first: it has the full history, decisions, live-chat findings, roadmap and risks.
> Settings and the brain (model) setup are in **[CUSTOMIZE.md](CUSTOMIZE.md)**.

## What it does today
- Branch-aware catalogue: per-branch prices and stock (money kept in integer kobo).
- Code-owned branch step: the customer names a city or area, picks a branch, confirms with YES.
- Code-owned quotes and reservations: the customer replies YES to a quote; stock is held for 2 hours
  plus a 30-minute grace period, then released automatically.
- At most 3 reservations per phone in any rolling 24 hours. Cancel and amend (each confirmed with YES).
- Order tracking, and human handoff for refunds and complaints.
- **Swappable brain:** hosted model lanes (primary, backup) and a local Ollama lane, with timeouts,
  retry, fallback, a daily spend budget, per-phone abuse limits, and a friendly "limited mode" if no
  model can answer.
- Per-turn diagnostics (`logs/turns.jsonl`: lane, tokens, cost, timings; never customer text).

## What is not built yet
Catalogue importer (CSV), per-shop settings, shop alerts, pay-now (design only), webhook hardening
(signature check, duplicate and media handling), a check that prices in replies match tool output, and
staff-side actions (mark ready or collected). See the roadmap in PROJECT_CONTEXT.md.

## How a customer message flows
```
WhatsApp -> Meta Cloud API -> FastAPI /webhook (app/main.py) -> run_agent (app/agent.py)
   1. Code paths first, no model: YES/NO to a branch, a cancel/amend, or a quote
   2. Per-phone limit check (model turns per hour)
   3. Model path: LaneRouter tries primary -> backup -> local (app/llm.py); tools in app/tools.py
   4. Guards check the model's wording; code-written text replaces the model's when needed
   5. State saved in SQLite; diagnostics line written
-> app/whatsapp.py sends the reply (prints to the console when no WhatsApp token is set)
```

## Quickstart (local development, Windows PowerShell)
Requires Python 3.10 or newer.

```powershell
# 1. Dependencies (run inside the ShopAssist-Chatbot folder)
pip install -r requirements.txt

# 2. Settings: copy the template once, then edit YOUR copy (.env is private and never committed)
Copy-Item .env.example .env

# 3. Pick a brain (see CUSTOMIZE.md, section 8)
#    Local (free, slow): install Ollama, then:  ollama pull llama3.1:8b
#    Hosted (fast):      pip install langchain-anthropic   (or langchain-openai for Groq, Gemini, ...)
#                        then add a key and a lane to .env

# 4. Check every lane with one tiny real call (explains what to fix in plain English)
python -m check_lanes            # add --models to list model names your key can use

# 5. Chat with the bot in the terminal
python -m chat                   # add --quiet to hide the developer lines

# 6. Or run the server (the WhatsApp token may stay empty: replies print to the console)
uvicorn app.main:app --reload --port 8000
```
On macOS or Linux use `cp .env.example .env`. The app reads `.env` itself; no `export` is needed.

## Tests
Run each from the project folder. No Ollama, network or API keys are needed (models are faked), and the suites ignore your private `.env` on purpose.
In PowerShell use the `python` prefix and backslashes.

| Command | Expected last line |
|---|---|
| `python tests\test_stage1.py` | `15/15 passed` |
| `python tests\test_stage2.py` | `17/17 passed` |
| `python tests\test_stage2_5.py` | `8/8 passed` |
| `python tests\test_stage2_5_1.py` | `5/5 passed` |
| `python tests\test_stage3.py` | `22/22 passed` |
| `python tests\test_stage3_1.py` | `19/19 passed` |
| `python tests\test_stage4.py` | `35/35 passed` |
| `python tests\test_stage4_1.py` | `43/43 passed` |
| `python tests\test_stage4_2.py` | `42/42 passed` |
| `python tests\test_stage4_2_1.py` | `18/18 passed` (224 in total) |

## Layout
| Path | Role |
|---|---|
| `app/main.py` | FastAPI app: webhook verify and receive; start-up loads the model lanes and starts the hold-release sweeper |
| `app/agent.py` | System prompt, the turn pipeline, guards wiring, limited mode, diagnostics capture |
| `app/llm.py` | Model lanes: providers, timeouts, retry, fallback, circuit breaker, lane loading |
| `app/budget.py` | Spend counting, daily cap, operator alerts once a day |
| `app/tools.py` | The tools the model may call (search, price check, quote, amend, cancel, track, handoff, branch lookup) |
| `app/orders.py` | Code-owned quotes, YES/NO parsing, reservations, cancel, amend, receipts |
| `app/branches.py` | Code-owned branch step texts and confirmation |
| `app/guards.py` | Reality checks on the model's wording about branches |
| `app/session.py` | Per-phone state kept in SQLite (branch lock, pending quote or action, history, handoff) |
| `app/db.py` | Schema, seed data, branch-aware queries, safe order writes, release of expired holds |
| `app/diagnostics.py` | Per-turn record (metadata only) |
| `app/textfmt.py`, `app/whatsapp.py`, `app/config.py` | Text helpers, Meta send client, settings |
| `chat.py`, `check_lanes.py` | Terminal chat; lane health check |
| `tests/` | Ten test suites (above) |
| `eval/test_queries.py` | Legacy canned-queries harness (needs a running model; not part of the suites) |

## Connect real WhatsApp (not yet hardened)
1. Create an app at developers.facebook.com, WhatsApp Business, and get a test number.
2. Expose your machine: `ngrok http 8000`.
3. In the Meta dashboard set the callback URL `https://<ngrok-host>/webhook` and the verify token to your `WHATSAPP_VERIFY_TOKEN`.
4. Put `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env` and restart.
5. Message the test number.

The webhook does **not** yet verify Meta's payload signature or filter duplicate or media messages:
do not point real customers at it until that stage is done.

## Production swap points
1. Sessions and data: SQLite to Postgres; session state to Redis if you run more than one server.
2. Webhook processing: add signature checks and a queue if volume grows.
3. Model: already a setting (lanes in `.env`); choose the production brain after a model comparison.
4. Move shop prices and stock from seed data to the catalogue importer (planned).
5. Keep `shop.db`, `logs/` and `.env` out of git (see `.gitignore`).
