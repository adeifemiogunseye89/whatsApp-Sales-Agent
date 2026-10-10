#!/usr/bin/env bash
# ShopAssist launcher — ./run.sh [dev|test]
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet -r requirements.txt
fi

# load .env if present
if [ -f .env ]; then export $(grep -v '^#' .env | xargs); fi

MODE=${1:-dev}
if [ "$MODE" = "test" ]; then
  echo "== Offline eval (no WhatsApp/Ollama needed beyond model) =="
  exec ./.venv/bin/python eval/test_queries.py
else
  echo "== Starting webhook on :8000 (set WHATSAPP_TOKEN in .env for live mode) =="
  exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi
