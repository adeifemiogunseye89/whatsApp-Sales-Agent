"""Per-turn diagnostics (Stage 4): how often does the model need correcting?

Writes ONE line of metadata per customer turn to logs/turns.jsonl. It never stores
what the customer wrote or what the bot replied - only which path the turn took,
how many model attempts it needed, which guards rejected a reply, which tools ran,
and how long it took. The phone number is stored only as a short hash.
This is the evidence for the model-choice decision (and for measuring cost per order).
"""
import hashlib
import json
import time
from pathlib import Path

from app import session as _session
from app.config import LOG_DIR


def new_turn() -> dict:
    return {"path": "model", "attempts": 0, "rejections": [], "tools": []}


def record(phone: str, diag: dict, started: float) -> None:
    diag["ms"] = int((time.monotonic() - started) * 1000)
    try:
        _session.get_session(phone).last_turn = dict(diag)    # shown by chat.py
    except Exception:
        pass
    entry = {"ts": int(time.time()),
             "who": hashlib.sha256(phone.encode()).hexdigest()[:8], **diag}
    try:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
        with open(Path(LOG_DIR) / "turns.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass          # logging must never break a customer's turn
