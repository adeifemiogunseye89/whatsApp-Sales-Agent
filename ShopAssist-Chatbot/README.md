# ShopAssist — WhatsApp Customer Service Agent (Skeleton)

FastAPI webhook + LangGraph ReAct agent + SQL tools, runnable fully on local Ollama.

## Architecture

```
WhatsApp user
   │  message
   ▼
Meta WhatsApp Cloud API ──POST──▶ FastAPI /webhook  (app/main.py)
                                      │ asyncio task (non-blocking)
                                      ▼
                               LangGraph agent  (app/agent.py)
                                  │  tool calls
                                  ▼
                               tools.py ──▶ SQLite (app/db.py)
                                          ──▶ session state (app/session.py)
                                          ──▶ optional human handoff
                                      │
                                      ▼ reply text
                               whatsapp.py ──▶ Meta send API (chunked messages)
```

## Quickstart (local dev)

```bash
# 1. Model
ollama pull llama3.1:8b

# 2. Deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Run (WhatsApp token empty -> replies print to console)
cp .env.example .env   # or export the vars
export $(grep -v '^#' .env | xargs)   # linux/mac
uvicorn app.main:app --reload --port 8000

# 4. Smoke-test the agent offline (no WhatsApp needed)
python eval/test_queries.py
```

## Connect real WhatsApp

1. Create an app at developers.facebook.com -> WhatsApp Business -> get a test number.
2. Expose locally: `ngrok http 8000`.
3. In Meta App Dashboard -> WhatsApp -> Configuration, set callback URL
   `https://<ngrok>/webhook`, verify token = your `WHATSAPP_VERIFY_TOKEN`.
4. Fill `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` in `.env`, restart.
5. Send a message to the test number.

## Layout

| Path | Role |
|---|---|
| `app/main.py` | FastAPI app, Meta webhook verify + receive, async dispatch |
| `app/agent.py` | System prompt + LangGraph ReAct agent bound to Ollama |
| `app/tools.py` | All LLM-callable tools (search, inventory, quote, order, track, escalate) |
| `app/db.py` | SQLite data layer + seed data + transactional order insert |
| `app/session.py` | Per-phone conversation state (SWAP FOR REDIS in production) |
| `app/whatsapp.py` | Meta send API client + message chunking (prints in dev mode) |
| `app/config.py` | Env-driven settings |
| `eval/test_queries.py` | Offline eval harness against the agent |

## Production swap points (deliberately marked SWAP in code)

1. `app/session.py` in-memory dict -> Redis (TTL, shared across instances).
2. `app/db.py` SQLite -> Postgres; `create_order` already transactional + idempotent.
3. Sync webhook processing -> queue (Celery/RQ/SQS) if volume grows.
4. `ChatOllama` -> LiteLLM/OpenRouter wrapper for hot-swapping to a cloud model.
5. Add auth/rate-limiting middleware before `/webhook` (Meta signs payloads).
