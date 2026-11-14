"""Spend control and operator alerts for the model lanes (Stage 4.1).

Why: a paid model must never be able to run up a bill the founder did not choose.
  * every model call is counted (tokens -> micro-dollars) per day and per lane
  * an alert goes out once a day at BUDGET_ALERT_PERCENT and again at 100%
  * at 100% PAID lanes are skipped (free lanes, such as local Ollama, still work)
Money is integer micro-dollars: dollars-per-million-tokens x tokens = micro-dollars.
"""
import os
from datetime import datetime

import httpx

from app import db
from app.config import BUDGET_ALERT_PERCENT, DAILY_BUDGET_USD, HUMAN_ESCALATION_WEBHOOK
from app.textfmt import WAT

# USD per million tokens (input, output). Override per lane with e.g.
#   LLM_PRICE_PRIMARY=0.10,0.50      (0,0 for a free tier)
KNOWN_PRICES = {"claude-haiku-5-5": (0.10, 0.50)}
UNKNOWN_HOSTED_PRICE = (1.0, 3.0)    # deliberately pessimistic: an unknown price must not hide spend


def today() -> str:
    return datetime.now(WAT).strftime("%Y-%m-%d")        # the shop's day (Lagos time)


def price_for(lane) -> tuple[float, float, bool]:
    """-> (input $/M, output $/M, assumed). `assumed` is True when we had to guess."""
    if lane.provider == "ollama":
        return 0.0, 0.0, False
    raw = os.getenv(f"LLM_PRICE_{lane.slot.upper()}", "").strip()
    if raw:
        try:
            a, b = raw.split(",")
            return float(a), float(b), False
        except ValueError:
            pass
    for name, (a, b) in KNOWN_PRICES.items():
        if name in lane.model.lower():
            return a, b, False
    return (*UNKNOWN_HOSTED_PRICE, True)


def is_paid(lane) -> bool:
    a, b, _ = price_for(lane)
    return a > 0 or b > 0


def cost_micro(lane, in_tokens: int, out_tokens: int) -> int:
    a, b, _ = price_for(lane)
    return round(in_tokens * a + out_tokens * b)


def _usd(x: float) -> str:
    return f"${x:.2f}" if x >= 0.01 else f"${x:g}"       # tiny budgets must not print as $0.00


def budget_micro() -> int:
    return round(DAILY_BUDGET_USD * 1_000_000)


def spent_micro(day: str | None = None) -> int:
    return db.llm_spent_micro(day or today())


def exhausted() -> bool:
    cap = budget_micro()
    return cap > 0 and spent_micro() >= cap


def lane_allowed(lane) -> bool:
    return not (is_paid(lane) and exhausted())


def notify_operator(kind: str, text: str) -> bool:
    """Tell the operator ONCE per day per kind (webhook if configured, else the console)."""
    if not db.alert_once(today(), kind):
        return False
    print(f"[operator alert] {text}")
    if HUMAN_ESCALATION_WEBHOOK:
        try:
            httpx.post(HUMAN_ESCALATION_WEBHOOK, json={"type": kind, "text": text}, timeout=10)
        except Exception as e:
            print(f"[operator alert failed] {e!r}")
    return True


def record(lane, in_tokens: int, out_tokens: int) -> int:
    """Count one model call; returns its cost in micro-dollars. Never raises."""
    try:
        cost = cost_micro(lane, in_tokens, out_tokens)
        db.add_llm_usage(today(), lane.label, 1, in_tokens, out_tokens, cost)
        cap = budget_micro()
        if cap > 0 and cost > 0:
            pct = spent_micro() * 100 / cap
            if pct >= 100:
                notify_operator("budget_exhausted",
                                f"Daily model budget of {_usd(DAILY_BUDGET_USD)} is used up. "
                                "Paid model lanes are paused until tomorrow.")
            elif pct >= BUDGET_ALERT_PERCENT:
                notify_operator("budget_alert",
                                f"{pct:.0f}% of today's {_usd(DAILY_BUDGET_USD)} model budget is used.")
        return cost
    except Exception as e:                      # accounting must never break a customer's reply
        print(f"[budget error] {e!r}")
        return 0
