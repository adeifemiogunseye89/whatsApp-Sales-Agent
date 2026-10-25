"""LangGraph ReAct agent on local Ollama (Update 1: multi-branch).

Defenses for weak local models:
1. Leak catcher: model prints a tool call as text -> we execute it ourselves.
2. Fake-order guard: model claims an order without create_order -> re-try.
3. GROUND-TRUTH FILTER: no reply containing an order ID / pickup code is
   shown to the customer unless that order REALLY exists in the database
   for this phone number. Fabrication becomes physically impossible.
"""
import json
import re
import uuid

from langchain_core.messages import AIMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from app import db as _db
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
- Before any prices, stock checks, quotes or orders, a pickup branch must be CONFIRMED. The system tracks this itself: shelf tools reply NO_BRANCH if none is confirmed.
- If the customer wants prices, wants to buy or reserve, and no branch is confirmed, ask: "Which city or area will the pickup happen in?"
- Call set_pickup_location with their answer. If it proposes ONE branch, show it and ask the customer to confirm (yes/no). If it lists several, ask which one they want, then call set_pickup_location again with that branch name.
- When they answer yes/no, call confirm_pickup_branch (accepted true or false).
- Once confirmed, do not ask again — unless the customer asks to switch branch; then call set_pickup_location again.

BRANCH RULES:
- All prices and stock you mention come from the CONFIRMED branch only. Never mix branches.
- If the confirmed branch is out of something, say so and offer to look at another branch (set_pickup_location with that branch name).

ORDERS & TRACKING:
- We do PAY-ON-PICKUP only. You never collect money in the chat.
- create_order RESERVES the order and RETURNS the order ID and pickup code.
- You can NEVER know an order ID or pickup code except from create_order's return. Never invent one.
- track_order works WITHOUT any location — call it directly whenever the customer asks about their order(s). If it returns nothing, honestly say no orders were found and offer to create one.

STRICT RULES:
1. NEVER invent product names, IDs, prices or stock numbers. If tools return nothing, say so.
2. NEVER calculate prices yourself — always use get_price_quote.
3. NEVER use create_order until the customer explicitly confirmed the exact items and total. Before that, restate the basket and total and ask: "Should I place this order?"
4. Currency uses two decimals.
5. If a tool result contains an error, read it carefully, fix your next call, and tell the customer politely what happened.

EXAMPLE CONVERSATION:
Customer: I want to buy some rice for my mum
Assistant: Lovely! Which city or area will she pick it up in?

Customer: Abuja
Assistant: (after set_pickup_location) Our branch in Abuja:
*Wuse Branch* — 14 Wuse Road, Wuse
Is this the right pickup branch? (yes/no)

Customer: yes
Assistant: (after confirm_pickup_branch) Locked in: Wuse Branch. From now on I'll quote Wuse Branch prices and stock.

Customer: do you have basmati rice?
Assistant: - P-1001: Royal Basmati Rice 5kg (price) (6 in stock)

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
_ORDER_CLAIM_RE = re.compile(r"ORD-[A-Z0-9]+|pickup code", re.I)


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


def _claims_match_database(phone: str, reply: str) -> bool:
    """GROUND TRUTH: if the reply mentions an order ID / pickup code, that
    order must really exist for this phone in the database."""
    if not _ORDER_CLAIM_RE.search(reply):
        return True  # no order claims -> fine
    orders = _db.orders_for_phone(phone)
    for o in orders:
        if o["order_id"] in reply or str(o["pickup_code"]) in reply:
            return True
    return False

def run_agent(phone: str, text: str) -> str | None:
    """One customer turn. Leak repair + fake-order guard + ground-truth
    filter. A reply only leaves this function if reality agrees with it."""
    set_context(phone)
    session = get_session(phone)
    if session.handoff_active:
        # A human owns this chat: record the message, stay silent.
        append_message(session, "user", text)
        session.save()
        return None
    append_message(session, "user", text)

    history = list(session.messages)   # ends with the customer's new message
    # Show the model the system's state, right before the customer's message.
    msgs = history[:-1] + [session.state_message()] + history[-1:]
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
        db_mismatch = not _claims_match_database(phone, reply)

        if leak is None and not fake_order and not db_mismatch:
            session.set_history(msgs)
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
        elif db_mismatch:
            # --- ground truth says the claimed order does NOT exist ---
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: REALITY CHECK FAILED. You mentioned an order "
                 "ID or pickup code, but NO such order exists in the database "
                 "for this customer. NEVER mention order IDs or codes except "
                 "what create_order or track_order actually returned. If the "
                 "customer wants an order, get confirmation and call "
                 "create_order. If they asked about status, call track_order "
                 "and report exactly what it returns (including 'none found')."},
            ]
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

    # emergency exit: NEVER hand over an unverified order claim
    session.set_history(msgs)
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and isinstance(m.content, str) \
                and m.content.strip() and not _ORDER_CLAIM_RE.search(m.content):
            return m.content
    return ("Sorry, I couldn't complete that just now. Let me connect you "
            "to a human agent — please hold on.")
