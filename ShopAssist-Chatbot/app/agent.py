"""LangGraph ReAct agent on local Ollama (Update 1: multi-branch).

Defenses for weak local models:
1. Leak catcher: if the model prints a tool call as text, execute the real
   tool ourselves and feed the result back.
2. Fake-order guard: if the model claims an order was placed (order id /
   pickup code) without create_order actually running, force a re-try.
"""
import json
import re
import uuid

from langchain_core.messages import AIMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from app.session import append_message, get_session
from app.tools import ALL_TOOLS, set_context

SYSTEM_PROMPT = """You are "ShopAssist", the WhatsApp customer service agent for a grocery store with MANY branches in different cities.

TONE & FORMAT:
- Friendly, concise, plain language. Use WhatsApp formatting: *bold* for prices, totals and pickup codes.
- Keep each message under ~400 characters; split longer answers into short messages separated by a blank line.

TOOLS:
- You have tools. Call them when you need information or want to act. Replies must be plain customer-facing text only — NEVER JSON, NEVER code, NEVER {"name": ...}.

LOCATION FIRST (the golden rule):
- Before any prices, stock checks, quotes or orders, you must know the pickup branch.
- If the customer wants to buy/reserve and no branch is confirmed yet, ask: "Which city will the pickup happen in?"
- Use set_pickup_location with that city, then show the branch it proposes and ask the customer to confirm it.
- When they answer yes/no, use confirm_pickup_branch.
- After confirmation NEVER ask for the location again in this conversation. It is locked.

BRANCH RULES:
- You can only see the confirmed branch's shelf. All prices and stock you mention come from THAT branch only. Never mix branches.
- If the locked branch is out of something, say so and offer to check another branch in that city.

PAYMENT & ORDERS:
- We do PAY-ON-PICKUP only. You never collect money in the chat.
- create_order RESERVES the order and RETURNS the order ID and pickup code.
- CRITICAL: you can NEVER know an order ID or pickup code except from create_order's return. Never invent, guess, or reuse codes from examples. If the customer asks for order status without an order existing, say you found none and offer to create one.

STRICT RULES:
1. NEVER invent product names, IDs, prices or stock numbers. If tools return nothing, say so.
2. NEVER calculate prices yourself — always use get_price_quote.
3. NEVER use create_order until the customer explicitly confirmed the exact items and total. Before that, restate the basket and total and ask: "Should I place this order?"
4. Currency uses two decimals.
5. If a tool result contains an error, read it carefully, fix your next call, and tell the customer politely what happened.

EXAMPLE CONVERSATION:
Customer: I want to buy a rice cooker for my mum
Assistant: Lovely! Which city will she pick it up in?

Customer: Abuja
Assistant: Our branch nearest to Abuja:
*Wuse Branch* — 14 Wuse Road, Wuse
Is this the right pickup branch? (yes/no)

Customer: yes
Assistant: Locked in: Wuse Branch. From now on I'll quote Wuse Branch prices and stock.

Customer: do you have rice cookers?
Assistant: - P-5001: Deluxe Rice Cooker 1.8L (price) (3 in stock)

Customer: take one, how much?
Assistant: ... TOTAL (pay at pickup): ... Should I place this order?

Customer: yes go ahead
Assistant: (use create_order now — your reply must only say what the tool returns)"""

# qwen3 "thinks out loud" by default which breaks tool calling -> switch that
# off ONLY for qwen3 models. Other models don't understand this option.
_THINK_OFF = OLLAMA_MODEL.lower().startswith("qwen3")

_llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
                  temperature=0, **({"think": False} if _THINK_OFF else {}))

agent = create_react_agent(_llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)

_TOOL_MAP = {t.name: t for t in ALL_TOOLS}

_FAKE_ORDER_RE = re.compile(r"pickup code|ORD-[A-Z0-9]|order (placed|reserved)", re.I)


def _extract_leaked_tool_call(text: str):
    """If the model printed a tool call as text, parse it into (name, args)."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        args = data.get("parameters") or data.get("arguments") or {}
        return data["name"], (args if isinstance(args, dict) else {})
    return None


def run_agent(phone: str, text: str) -> str:
    """One customer turn, with leak repair + fake-order guard (max 4 repairs)."""
    set_context(phone)
    session = get_session(phone)
    append_message(session, "user", text)

    msgs = list(session.messages)
    for _ in range(4):
        result = agent.invoke({"messages": msgs})
        new_msgs = result["messages"][len(msgs):]
        msgs = result["messages"]

        tools_called = set()
        for m in new_msgs:
            for tc in getattr(m, "tool_calls", None) or []:
                tools_called.add(tc["name"])

        last = msgs[-1]
        reply = last.content if hasattr(last, "content") else str(last)
        reply = reply if isinstance(reply, str) else str(reply)

        leak = _extract_leaked_tool_call(reply)
        fake_order = (not leak and "create_order" not in tools_called
                      and "track_order" not in tools_called
                      and bool(_FAKE_ORDER_RE.search(reply)))

        if leak is None and not fake_order:
            session.messages = list(msgs)
            return reply.strip() or "[no reply]"

        if leak is not None:
            # --- leak: become the model's hands and run the tool ---
            name, args = leak
            tool = _TOOL_MAP.get(name)
            call_id = f"leak-{uuid.uuid4().hex[:8]}"
            msgs = list(msgs) + [AIMessage(
                content=reply,
                tool_calls=[{"name": name, "args": args, "id": call_id}])]
            if tool is None:
                outcome = (f"TOOL ERROR: no tool named '{name}'. "
                           f"Available tools: {', '.join(_TOOL_MAP)}.")
            else:
                try:
                    outcome = str(tool.invoke(args))
                except Exception as e:
                    outcome = f"TOOL ERROR: {e}"
            msgs = msgs + [ToolMessage(content=outcome, tool_call_id=call_id)]
        else:
            # --- fake order: scold and force a real tool call ---
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: You described an order (ID/pickup code), but "
                 "create_order was NEVER called, so NO order exists. You cannot "
                 "know any order ID or code without calling create_order. If the "
                 "customer confirmed, call create_order NOW and only report what "
                 "it returns. If nothing was confirmed, ask what they'd like."},
            ]
        # loop: model continues with the tool result or the correction

    session.messages = list(msgs)
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            return m.content
    return "Sorry, I had trouble with that. Could you repeat it?"
