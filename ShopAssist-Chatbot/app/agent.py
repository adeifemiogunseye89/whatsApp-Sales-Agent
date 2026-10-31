"""LangGraph ReAct agent on local Ollama (Update 1: multi-branch).

Defenses for weak local models:
1. Leak catcher: model prints a tool call as text -> we execute it ourselves.
2. Fake-order guard: model claims an order it never looked up -> re-try.
   (Stage 3: the model cannot place orders at all; orders.py does, after YES.)
2b. Fake-action guard (Stage 4): model says a reservation was cancelled or changed
   without the system having done it -> re-try.
3. GROUND-TRUTH FILTER: no reply containing an order ID / pickup code is
   shown to the customer unless that order REALLY exists in the database
   for this phone number. Fabrication becomes physically impossible.
"""
import json
import re
import time
import uuid

from langchain_core.messages import AIMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from app import db as _db
from app import branches, diagnostics, guards, orders
from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL
from app.session import append_message, get_session
from app.textfmt import to_whatsapp
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
- Call set_pickup_location with their answer. The SYSTEM then shows the customer the branch (or the list of branches) and asks them to reply YES or NO itself. The SYSTEM confirms the branch; you cannot.
- NEVER write branch names or addresses yourself. They come only from set_pickup_location. If the customer asks which branches exist, call set_pickup_location with their city or area.
- Once confirmed, do not ask again — unless the customer asks to switch branch; then call set_pickup_location again.

BRANCH RULES:
- All prices and stock you mention come from the CONFIRMED branch only. Never mix branches.
- If the confirmed branch is out of something, say so and offer to look at another branch (set_pickup_location with that branch name).

ORDERS & TRACKING:
- We do PAY-ON-PICKUP only. You never collect money in the chat.
- To price a basket for a NEW reservation, call get_price_quote with the exact items and quantities. The SYSTEM then shows the customer an itemised quote and places the order itself when they reply YES. You cannot place orders.
- If the customer already has an open reservation (listed in the system state) and wants to change it ("make it 3 instead", "add one more", "change to ..."), call amend_order with the FULL new basket. To cancel one, call cancel_order. NEVER create a new reservation to replace an old one.
- After get_price_quote, amend_order or cancel_order, add nothing: the system has already shown the customer the request.
- Each phone can make 3 reservations in any 24 hours; cancelled and expired ones still count. The system tells the customer when the limit is reached.
- You can NEVER know an order ID or pickup code except from track_order or the system state. Never invent one.
- track_order works WITHOUT any location — call it directly whenever the customer asks about their order(s). If it returns nothing, honestly say no orders were found and offer to price what they want.

STRICT RULES:
1. NEVER invent product names, IDs, prices or stock numbers. If tools return nothing, say so.
2. NEVER calculate prices yourself — always use get_price_quote.
3. NEVER say an order is placed, reserved, confirmed, cancelled or changed. Only the system does these, after the customer replies YES.
4. Copy prices exactly as the tools give them.
5. If a tool result contains an error, read it carefully, fix your next call, and tell the customer politely what happened.
6. NEVER say a pickup branch is set, locked or confirmed. Only the system does that.
"""

# qwen3 "thinks out loud" by default which breaks tool calling -> switch that
# off ONLY for qwen3 models. Other models don't understand this option.
_THINK_OFF = OLLAMA_MODEL.lower().startswith("qwen3")

_llm = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_BASE_URL,
                  temperature=0, **({"think": False} if _THINK_OFF else {}))

agent = create_react_agent(_llm, ALL_TOOLS, prompt=SYSTEM_PROMPT)

_TOOL_MAP = {t.name: t for t in ALL_TOOLS}

# Invented order ids / codes are caught by _claims_match_database, so this only needs the
# wording of a claim. (Real open order ids now appear in the system state, so a bare id
# in a reply is no longer suspicious by itself.)
_FAKE_ORDER_RE = re.compile(
    r"order (has been )?(placed|reserved|confirmed)|(has|have) been (placed|reserved)"
    r"|your order is (placed|reserved|confirmed)", re.I)
_FAKE_ACTION_RE = re.compile(
    r"(?:has been|have been|was|is now|successfully)\s+(?:cancell?ed|amended|changed|updated)\b"
    r"|\bI(?:'|’)?ve\s+(?:cancell?ed|amended|changed|updated)\b"
    r"|\bI\s+(?:have\s+)?(?:cancell?ed|amended|changed|updated)\s+(?:your|the)\b", re.I)
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


def _with_final_reply(msgs: list, text: str) -> list:
    """Replace the model's closing message with the code-written one, so chat
    history records what the customer was actually shown."""
    msgs = list(msgs)
    last = msgs[-1]
    if isinstance(last, AIMessage) and not last.tool_calls:
        msgs[-1] = AIMessage(content=text)
    else:
        msgs.append(AIMessage(content=text))
    return msgs


def run_agent(phone: str, text: str) -> str | None:
    """One customer turn, with per-turn diagnostics (path, model attempts, guard
    rejections, tools, time - never the customer's words). See _run_agent."""
    diag = diagnostics.new_turn()
    started = time.monotonic()
    try:
        return _run_agent(phone, text, diag)
    except Exception:
        diag["path"] = "error"
        raise
    finally:
        diagnostics.record(phone, diag, started)


def _run_agent(phone: str, text: str, diag: dict) -> str | None:
    """Order of authority:
      0. expired reservations are released (stock goes back on the shelf)
      1. handoff active           -> stay silent
      2. YES/NO to a branch       -> CODE answers (branches.py); on YES the model
         proposal                    then carries on with the original request
      3. YES/NO to cancel/change  -> CODE answers (orders.py), model not involved
      4. YES/NO to a quote        -> CODE answers (orders.py), model not involved
      5. model turn               -> a tool-written reply (outbox) is shown
                                     verbatim instead of the model's wording
      6. otherwise                -> leak repair + order / action / branch guards +
                                     ground-truth filter. Replies that fail a
                                     guard are NEVER saved to chat history."""
    set_context(phone)
    session = get_session(phone)
    if session.handoff_active:
        # A human owns this chat: record the message, stay silent.
        diag["path"] = "handoff"
        append_message(session, "user", text)
        session.save()
        return None
    try:
        _db.release_expired()                  # cheap no-op when nothing is due
    except Exception as e:
        print(f"[release_expired error] {e!r}")
    session.take_outbox()                      # clear anything left from a failed turn
    session.branch_just_confirmed = False
    append_message(session, "user", text)

    lead = None   # code-written opening line, e.g. "Pickup branch confirmed: ..."
    if session.branch_awaiting_reply():
        decision = orders.parse_confirmation(text)
        if decision == "no":
            diag["path"] = "code:branch_no"
            reply = branches.reject_proposal(session)
            append_message(session, "assistant", reply)
            session.save()
            return reply
        if decision == "yes":
            diag["path"] = "code:branch_yes+model"
            lead = branches.confirm_proposal(session)   # model resumes the request below

    if lead is None and session.action_awaiting_reply():
        decision = orders.parse_confirmation(
            text, cancel_means_yes=session.pending_action["type"] == "cancel")
        if decision:
            diag["path"] = f"code:action_{decision}"
            reply = (orders.confirm_action(session) if decision == "yes"
                     else orders.decline_action(session))
            append_message(session, "assistant", reply)
            session.save()
            return reply

    if lead is None and session.quote_awaiting_reply():
        decision = orders.parse_confirmation(text)
        if decision:
            diag["path"] = f"code:quote_{decision}"
            reply = (orders.confirm_pending(session) if decision == "yes"
                     else orders.cancel_pending(session))
            append_message(session, "assistant", reply)
            session.save()
            return reply

    def shown(body: str) -> str:
        return f"{lead}\n\n{body}" if lead else body

    history = list(session.messages)   # ends with the customer's new message
    # Show the model the system's state, right before the customer's message.
    turn_base = history[:-1] + [session.state_message()] + history[-1:]
    msgs = turn_base
    for _ in range(4):
        diag["attempts"] += 1
        result = agent.invoke({"messages": msgs})
        new_msgs = result["messages"][len(msgs):]
        msgs = result["messages"]

        tools_called = set()
        for m in new_msgs:
            for tc in getattr(m, "tool_calls", None) or []:
                tools_called.add(tc["name"])
        for t in sorted(tools_called):
            if t not in diag["tools"]:
                diag["tools"].append(t)

        code_reply = session.take_outbox()
        if code_reply:                         # a tool wrote the customer's message
            diag["path"] = "outbox" + ("+lead" if lead else "")
            final = shown(code_reply)
            session.set_history(_with_final_reply(turn_base + new_msgs, final))
            return final

        last = msgs[-1]
        reply = last.content if hasattr(last, "content") else str(last)
        reply = reply if isinstance(reply, str) else str(reply)

        leak = _extract_leaked_tool_call(reply)
        fake_order = (not leak and "track_order" not in tools_called
                      and bool(_FAKE_ORDER_RE.search(reply)))
        fake_action = (not leak and not (tools_called & {"track_order", "cancel_order", "amend_order"})
                       and bool(_FAKE_ACTION_RE.search(reply)))
        db_mismatch = not _claims_match_database(phone, reply)
        tool_text = " ".join(m.content for m in msgs[len(turn_base):]
                             if isinstance(m, ToolMessage) and isinstance(m.content, str))
        branch_issue = (None if leak else
                        guards.bad_branch_claim(session, reply, tools_called, tool_text))

        if (leak is None and not fake_order and not fake_action
                and not db_mismatch and not branch_issue):
            body = to_whatsapp(reply.strip())
            if not body:
                body = "What would you like to order?" if lead else "[no reply]"
            final = shown(body)
            # Save only THIS attempt: earlier rejected attempts never reach history.
            session.set_history(_with_final_reply(turn_base + new_msgs, final))
            return final
        if leak is not None:
            # --- leak: become the model's hands and run the tool ---
            diag["rejections"].append("leak")
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
            diag["rejections"].append("order_mismatch")
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: REALITY CHECK FAILED. You mentioned an order "
                 "ID or pickup code, but NO such order exists in the database "
                 "for this customer. NEVER mention order IDs or codes except "
                 "what track_order or the system state actually showed. You cannot "
                 "place orders: if the customer wants to buy, call get_price_quote. "
                 "If they asked about status, call track_order and report exactly "
                 "what it returns (including 'none found')."},
            ]
        elif branch_issue:
            # --- the reply talks about branches the database does not back up ---
            diag["rejections"].append("branch:" + branch_issue.split(" (")[0][:40])
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: BRANCH CHECK FAILED. " + branch_issue + " Branch "
                 "names and addresses come ONLY from set_pickup_location, and only "
                 "the system confirms a branch. Do not write branch names or "
                 "addresses, and do not say a branch is set. If the customer asked "
                 "about branches or named a place, call set_pickup_location with "
                 "it; otherwise answer without mentioning branches."},
            ]
        elif fake_action:
            # --- the model claims a cancel/change the system never did ---
            diag["rejections"].append("fake_action")
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: You said a reservation was cancelled or changed, but you "
                 "cannot do that: only the system does, after the customer replies YES. "
                 "To cancel call cancel_order; to change items call amend_order."},
            ]
        else:
            # --- fake order: scold and force a real tool call ---
            diag["rejections"].append("fake_order")
            msgs = list(msgs) + [
                AIMessage(content=reply),
                {"role": "user", "content":
                 "SYSTEM NOTICE: You described an order as placed or reserved, but "
                 "you cannot place orders, so NO order exists. Only the system "
                 "places orders, after the customer replies YES to its quote. "
                 "If the customer wants to buy, call get_price_quote. If they "
                 "asked about an existing order, call track_order."},
            ]
        # loop: model continues with the tool result or the correction

    # Emergency exit: nothing the model said passed the checks, so show NONE of it.
    diag["path"] = "fallback"
    final = shown("What would you like to order?") if lead else (
        "Sorry, I couldn't complete that just now. Please try again, or tell me "
        "if you'd like a human agent.")
    session.set_history(turn_base + [AIMessage(content=final)])
    return final
