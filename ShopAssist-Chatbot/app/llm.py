"""Model lanes (Stage 4.1): the bot's "brain" is a setting, not a code change.

Why: a 14B model on a 2 GB-GPU laptop took 5-8 minutes per turn. Production needs
a brain that answers in seconds, costs almost nothing and survives a provider outage.

How it works
  * Up to three LANES, each "provider:model", tried in order:  primary -> backup -> local
      LLM_PRIMARY=anthropic:claude-haiku-5-5      (paid, fast)
      LLM_BACKUP=groq:llama-3.3-70b-versatile     (a different provider)
      LLM_LOCAL=ollama:llama3.1:8b                (free but slow: last resort)
  * Hosted lanes get a short timeout and one retry; then the next lane is used.
  * A lane that fails with a bad key / no credit / rate limit is paused for a while
    (circuit breaker) and the operator is told once a day.
  * Paid lanes are skipped when today's budget is used up (see budget.py).
  * If every lane fails the caller gets LaneUnavailable and shows the limited-mode message.
  * The guards in agent.py apply to every lane, whatever model is behind it.
"""
import os
import threading
import time
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from app import budget
from app.config import (LLM_BACKUP, LLM_LOCAL, LLM_MAX_OUTPUT_TOKENS, LLM_PRIMARY,
                        LLM_TIMEOUT_HOSTED, LLM_TIMEOUT_LOCAL, OLLAMA_BASE_URL,
                        OLLAMA_KEEP_ALIVE, OLLAMA_NUM_CTX)
from app.session import STATE_MSG_NAME

SUPPORTED = ("anthropic", "openai", "groq", "gemini", "openrouter", "mistral", "cerebras", "ollama")
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY",
           "gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "mistral": "MISTRAL_API_KEY", "cerebras": "CEREBRAS_API_KEY"}
# OpenAI-compatible endpoints (verify against each provider's docs before relying on them)
BASE_URL = {"groq": "https://api.groq.com/openai/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "openrouter": "https://openrouter.ai/api/v1",
            "mistral": "https://api.mistral.ai/v1",
            "cerebras": "https://api.cerebras.ai/v1"}


class MissingKey(Exception):
    pass


class MissingPackage(Exception):
    pass


class LaneUnavailable(Exception):
    """No lane could answer. `tried` is [(lane label, reason), ...]."""
    def __init__(self, tried):
        super().__init__("no model lane available: " + (", ".join(f"{a}={b}" for a, b in tried) or "none configured"))
        self.tried = tried


@dataclass(frozen=True)
class Lane:
    slot: str        # primary | backup | local
    provider: str
    model: str

    @property
    def hosted(self) -> bool:
        return self.provider != "ollama"

    @property
    def label(self) -> str:
        return f"{self.slot}:{self.provider}:{self.model}"


@dataclass
class Use:
    """What one model call used (for diagnostics, cost and speed measurement)."""
    lane: Lane
    ms: int
    in_tokens: int
    out_tokens: int
    cost_micro: int
    load_ms: int = 0
    prefill_ms: int = 0
    decode_ms: int = 0


def parse_lane(slot: str, spec: str) -> Lane | None:
    spec = (spec or "").strip()
    if not spec:
        return None
    provider, sep, model = spec.partition(":")        # split ONCE: ollama models contain ':'
    provider = provider.strip().lower()
    if not sep or not model.strip():
        raise ValueError(f"LLM_{slot.upper()}='{spec}' must look like provider:model "
                         "(for example anthropic:claude-haiku-5-5)")
    if provider not in SUPPORTED:
        raise ValueError(f"unknown provider '{provider}' in LLM_{slot.upper()}; "
                         f"supported: {', '.join(SUPPORTED)}")
    return Lane(slot, provider, model.strip())


def configured_lanes() -> list[Lane]:
    lanes = []
    for slot, spec in (("primary", LLM_PRIMARY), ("backup", LLM_BACKUP), ("local", LLM_LOCAL)):
        try:
            lane = parse_lane(slot, spec)
        except ValueError as e:
            print(f"[llm] ignoring lane: {e}")
            continue
        if lane:
            lanes.append(lane)
    return lanes


def has_key(lane: Lane) -> bool:
    return (not lane.hosted) or bool(os.getenv(KEY_ENV[lane.provider], "").strip())


def _supported_kwargs(cls, wanted: dict) -> dict:
    """Pass only options this installed version of the class understands, so an older
    langchain package can never stop the app from starting."""
    fields = getattr(cls, "model_fields", {}) or {}
    names = set(fields)
    for f in fields.values():
        if getattr(f, "alias", None):
            names.add(f.alias)
        if isinstance(getattr(f, "validation_alias", None), str):
            names.add(f.validation_alias)
    return {k: v for k, v in wanted.items() if k in names}


def make_chat_model(lane: Lane):
    if lane.provider == "ollama":
        from langchain_ollama import ChatOllama
        wanted = {"model": lane.model, "base_url": OLLAMA_BASE_URL, "temperature": 0,
                  "num_ctx": OLLAMA_NUM_CTX, "keep_alive": OLLAMA_KEEP_ALIVE,
                  "num_predict": LLM_MAX_OUTPUT_TOKENS,
                  "client_kwargs": {"timeout": LLM_TIMEOUT_LOCAL}}
        if lane.model.lower().startswith("qwen3"):
            wanted["think"] = False                      # qwen3 "thinking" is far too slow here
        return ChatOllama(**_supported_kwargs(ChatOllama, wanted))
    key = os.getenv(KEY_ENV[lane.provider], "").strip()
    if not key:
        raise MissingKey(f"{KEY_ENV[lane.provider]} is not set")
    common = {"model": lane.model, "api_key": key, "timeout": LLM_TIMEOUT_HOSTED,
              "max_retries": 0, "max_tokens": LLM_MAX_OUTPUT_TOKENS, "temperature": 0}
    if lane.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise MissingPackage("run:  pip install langchain-anthropic") from e
        return ChatAnthropic(**_supported_kwargs(ChatAnthropic, common))
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise MissingPackage("run:  pip install langchain-openai") from e
    if lane.provider in BASE_URL:
        common["base_url"] = BASE_URL[lane.provider]
    return ChatOpenAI(**_supported_kwargs(ChatOpenAI, common))


def classify(e: Exception) -> str:
    """auth | billing | rate_limit | transient | other"""
    name = type(e).__name__.lower()
    text = str(e).lower()
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403) or "authentication" in name or "invalid api key" in text:
        return "auth"
    if status == 402 or "credit balance" in text or "insufficient_quota" in text or "billing" in text:
        return "billing"
    if status == 429 or "ratelimit" in name or "rate limit" in text:
        return "rate_limit"
    if (isinstance(status, int) and status >= 500) or "timeout" in name or "timed out" in text \
            or "connect" in name or "overloaded" in text or isinstance(e, (TimeoutError, ConnectionError)):
        return "transient"
    return "other"


def fold_state_note(msgs: list) -> list:
    """Some providers reject a system message in the MIDDLE of a conversation. Fold the
    per-turn state note into the customer message that follows it instead."""
    out, pending = [], None
    for m in msgs:
        if isinstance(m, SystemMessage) and getattr(m, "name", None) == STATE_MSG_NAME:
            pending = m.content
            continue
        if pending is not None and isinstance(m, HumanMessage):
            m = HumanMessage(content=f"<system_state>\n{pending}\n</system_state>\n\n{m.content}")
            pending = None
        out.append(m)
    if pending is not None:
        out.append(HumanMessage(content=f"<system_state>\n{pending}\n</system_state>"))
    return out


def shape_for_lane(lane: Lane, msgs: list) -> list:
    return fold_state_note(msgs) if lane.hosted else list(msgs)


def _text(m) -> str:
    c = m["content"] if isinstance(m, dict) else getattr(m, "content", "")
    return c if isinstance(c, str) else str(c)


def usage_from(new_msgs: list) -> tuple[int, int, int, int, int]:
    """-> (input tokens, output tokens, load ms, prefill ms, decode ms) summed over the AI messages."""
    i = o = load = pre = dec = 0
    for m in new_msgs:
        if getattr(m, "type", "") != "ai":
            continue
        u = getattr(m, "usage_metadata", None) or {}
        md = getattr(m, "response_metadata", None) or {}
        if u:
            i += int(u.get("input_tokens", 0))
            o += int(u.get("output_tokens", 0))
        elif md.get("prompt_eval_count") is not None:          # Ollama's own counts
            i += int(md.get("prompt_eval_count") or 0)
            o += int(md.get("eval_count") or 0)
        load += int(md.get("load_duration", 0) or 0) // 1_000_000
        pre += int(md.get("prompt_eval_duration", 0) or 0) // 1_000_000
        dec += int(md.get("eval_duration", 0) or 0) // 1_000_000
    return i, o, load, pre, dec


class LaneRouter:
    """Drop-in for the old `agent`: .invoke({"messages": [...]}) -> {"messages": [...]}."""

    def __init__(self, prompt=None, tools=None, lanes=None, graph_builder=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.prompt, self.tools = prompt, list(tools or [])
        self.lanes = configured_lanes() if lanes is None else list(lanes)
        self._builder = graph_builder or self._real_graph
        self._graphs, self._down_until, self._fails = {}, {}, {}
        self._clock, self._sleep = clock, sleep
        self._tl = threading.local()                          # per-thread: last use + errors

    # ----- read-only helpers -----
    @property
    def last(self) -> Use | None:
        return getattr(self._tl, "last", None)

    @property
    def last_errors(self) -> list:
        return getattr(self._tl, "errors", [])

    def describe(self) -> list[str]:
        if not self.lanes:
            return ["no model lane configured: set LLM_PRIMARY or LLM_LOCAL"]
        out = []
        for lane in self.lanes:
            state = "ready" if has_key(lane) else f"OFF ({KEY_ENV.get(lane.provider)} not set)"
            a, b, assumed = budget.price_for(lane)
            price = "free" if not (a or b) else f"${a:g}/${b:g} per M tokens" + (" (ASSUMED: set LLM_PRICE_" + lane.slot.upper() + ")" if assumed else "")
            out.append(f"{lane.label}: {state}, {price}")
        return out

    def available_lanes(self) -> list[Lane]:
        now = self._clock()
        return [ln for ln in self.lanes
                if has_key(ln) and self._down_until.get(ln.label, 0) <= now and budget.lane_allowed(ln)]

    # ----- building -----
    def _real_graph(self, lane: Lane):
        from langgraph.prebuilt import create_react_agent
        return create_react_agent(make_chat_model(lane), self.tools, prompt=self.prompt)

    def _graph(self, lane: Lane):
        if lane.label not in self._graphs:
            self._graphs[lane.label] = self._builder(lane)
        return self._graphs[lane.label]

    # ----- failures -----
    def _failed(self, lane: Lane, kind: str, err: Exception) -> None:
        label, now = lane.label, self._clock()
        print(f"[llm] {label} failed ({kind}): {err!r}")
        if kind in ("auth", "billing"):
            self._down_until[label] = now + 1800
            budget.notify_operator(f"lane_{kind}:{label}",
                                   f"Model lane {label} stopped ({kind}). Check its API key and credit.")
        elif kind == "rate_limit":
            self._down_until[label] = now + 60
        elif kind == "transient":
            self._fails[label] = self._fails.get(label, 0) + 1
            if self._fails[label] >= 3:
                self._down_until[label] = now + 60
                self._fails[label] = 0

    # ----- the call -----
    def invoke(self, inp: dict, config=None) -> dict:
        msgs = list(inp["messages"])
        self._tl.last, self._tl.errors = None, []
        tried: list[tuple[str, str]] = []
        for lane in self.available_lanes():
            for attempt in range(2 if lane.hosted else 1):
                started = self._clock()
                shaped = shape_for_lane(lane, msgs)
                try:
                    graph = self._graph(lane)
                    result = graph.invoke({"messages": shaped}, config) if config else graph.invoke({"messages": shaped})
                except Exception as e:                       # classify, then retry or move on
                    kind = classify(e)
                    self._failed(lane, kind, e)
                    tried.append((lane.label, kind))
                    if kind != "transient":
                        break
                    self._sleep(0.5)
                    continue
                self._fails[lane.label] = 0
                ms = int((self._clock() - started) * 1000)
                new = list(result["messages"])[len(shaped):]
                self._tl.last = self._usage(lane, shaped, new, ms)
                self._tl.errors = tried
                return {"messages": msgs + new}              # indexes stay valid for the caller
        self._tl.errors = tried
        raise LaneUnavailable(tried)

    def _usage(self, lane: Lane, shaped: list, new: list, ms: int) -> Use:
        i, o, load, pre, dec = usage_from(new)
        if lane.hosted and not (i or o):                    # provider gave no counts: estimate
            calls = max(1, sum(1 for m in new if getattr(m, "type", "") == "ai"))
            i = sum(len(_text(m)) for m in shaped) // 4 * calls
            o = sum(len(_text(m)) for m in new if getattr(m, "type", "") == "ai") // 4
        return Use(lane, ms, i, o, budget.record(lane, i, o), load, pre, dec)

    def warm_up(self) -> bool:
        """Load the local model into memory before the first customer arrives.
        Only when the local model is the FIRST lane (otherwise it would waste memory)."""
        lanes = self.available_lanes()
        if not lanes or lanes[0].hosted:
            return False
        try:
            make_chat_model(lanes[0]).invoke("hi")
            return True
        except Exception as e:
            print(f"[llm] warm-up skipped: {e!r}")
            return False
