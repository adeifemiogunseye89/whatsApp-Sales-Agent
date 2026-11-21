"""Check every model lane with ONE tiny real call and say, in plain English, what to fix.

Run from the project folder:
    python -m check_lanes             test each lane in your .env (primary, backup, local)
    python -m check_lanes --models    also list the model names each key can use

Why: a lane can be "ready" in the banner and still fail on the first real message
(no credit, a retired model name, a rate limit). This finds out BEFORE a customer does.
It makes one call of a few tokens per lane: a fraction of a naira, nothing if the lane is free.
"""
import os
import sys
import time

import httpx

from app import budget, llm
from app.config import LLM_TIMEOUT_LOCAL, OLLAMA_BASE_URL


def _http_get(url: str, headers: dict | None = None) -> dict:
    r = httpx.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def list_models(lane, http_get=_http_get, limit: int = 40) -> list[str]:
    """The model names this lane's key can actually use (empty if it cannot be listed)."""
    try:
        if lane.provider == "ollama":
            data = http_get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags")
            return sorted(m["name"] for m in data.get("models", []))[:limit]
        key = os.getenv(llm.KEY_ENV[lane.provider], "")
        if lane.provider == "anthropic":
            data = http_get("https://api.anthropic.com/v1/models",
                            {"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            base = llm.BASE_URL.get(lane.provider, "https://api.openai.com/v1")
            data = http_get(base.rstrip("/") + "/models", {"Authorization": f"Bearer {key}"})
        return sorted(m["id"] for m in data.get("data", []))[:limit]
    except Exception:
        return []


def explain(kind: str, lane, err: Exception, wait: float | None = None, models: list[str] | None = None) -> str:
    """Plain-English cause and fix for a failed lane."""
    key_name = llm.KEY_ENV.get(lane.provider, "")
    if kind == "no_key":
        return f"No API key found. Add {key_name}=... to your .env file (see CUSTOMIZE.md, section 8)."
    if kind == "no_package":
        return f"A needed package is missing. {err}"
    if kind == "auth":
        return ("The provider rejected the key. Check it was copied completely (no spaces or quotes), "
                "has not been revoked, and belongs to this provider. If unsure, create a new key in "
                "the provider's console and update .env.")
    if kind == "billing":
        return ("The account has no usable credit or has reached its spending limit. Add credit on the "
                "provider's billing page, or use a different lane. (A chat subscription such as Claude "
                "Pro is separate from API credit.)")
    if kind == "rate_limit":
        extra = f" It asked for a wait of about {wait:g} seconds." if wait else ""
        return ("The provider's request or token limit was reached." + extra + " Free tiers are small: "
                "wait a minute and run this check again, or move to a paid tier or add a backup provider.")
    if kind == "transient":
        if lane.provider == "ollama":
            return (f"Ollama is not answering at {OLLAMA_BASE_URL}. Open the Ollama app (or run: ollama serve), "
                    "then check the model name with: ollama list")
        return "Could not reach the provider (network problem or provider outage). Check your internet connection and retry."
    text = str(err).lower()
    if "not found" in text or "does not exist" in text or "404" in text:
        shown = (" Models this key can use: " + ", ".join(models[:15]) + ".") if models else \
                " Run this check with --models to list what your key can use."
        return (f"This key cannot use the model name '{lane.model}' (it may be retired, mistyped, or not "
                f"included in your plan).{shown} Then fix the lane in .env.")
    return f"Unexpected problem: {type(err).__name__}: {err}"


def check_lane(lane, factory=llm.make_chat_model, lister=list_models, clock=time.monotonic,
               show_models: bool = False) -> dict:
    started = clock()
    wait = None
    try:
        reply = factory(lane).invoke("Reply with the single word OK.")
    except Exception as e:
        if isinstance(e, llm.MissingKey):
            kind = "no_key"
        elif isinstance(e, llm.MissingPackage):
            kind = "no_package"
        else:
            kind = llm.classify(e)
            wait = llm.retry_after(e) if kind == "rate_limit" else None
        models = lister(lane) if (show_models or kind == "other") else None
        return {"lane": lane.label, "ok": False, "kind": kind, "ms": int((clock() - started) * 1000),
                "fix": explain(kind, lane, e, wait, models), "models": models or []}
    i, o, *_ = llm.usage_from([reply])
    return {"lane": lane.label, "ok": True, "ms": int((clock() - started) * 1000), "in": i, "out": o,
            "cost_micro": budget.cost_micro(lane, i, o), "text": str(getattr(reply, "content", "")).strip()[:40],
            "models": lister(lane) if show_models else []}


def run(lanes=None, factory=llm.make_chat_model, lister=list_models, show_models: bool = False, out=print) -> int:
    lanes = llm.configured_lanes() if lanes is None else lanes
    if not lanes:
        out("No model lane is configured. Set LLM_PRIMARY (hosted) or LLM_LOCAL (Ollama) in your .env file.")
        return 1
    good = 0
    for lane in lanes:
        out(f"\n{lane.label}")
        if not lane.hosted:
            out(f"  (a local model can take a minute or more the first time; waiting up to {LLM_TIMEOUT_LOCAL}s)")
        r = check_lane(lane, factory, lister, show_models=show_models)
        if r["ok"]:
            good += 1
            out(f"  OK in {r['ms'] / 1000:.1f}s, replied '{r['text']}', {r['in']} tokens in / {r['out']} out, "
                f"about ${r['cost_micro'] / 1_000_000:.5f}")
        else:
            out(f"  FAILED ({r['kind']}) after {r['ms'] / 1000:.1f}s")
            out(f"  Fix: {r['fix']}")
        if r["models"] and (show_models or not r["ok"]):
            out("  Models available to this lane: " + ", ".join(r["models"][:25]))
    out("")
    out(f"{good} of {len(lanes)} lane(s) working." + ("" if good else " Fix the lines marked 'Fix:' above, then run this again."))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(run(show_models="--models" in sys.argv))
