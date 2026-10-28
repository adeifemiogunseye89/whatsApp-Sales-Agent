"""Per-phone conversation state, persisted in the database (Stage 2).

What lives here and why:
  * branch lock, proposed branch, last order key, handoff -> CODE-OWNED state.
    It must survive a restart, so every change is saved to the `sessions` table.
  * chat history -> the model's short-term memory, also saved so a customer
    mid-order isn't reset by a deploy. Trimmed so it never starts mid tool-call.

How it works: one live Session object per phone per process (`_cache`), written
through to the DB on every state change. Reads never hit the DB after the first.

LIMITS (known, deliberate for now): run ONE server process. Two workers would
each hold their own cache and overwrite each other. SWAP `_cache` + the db.*
session functions for Redis when you scale out; the Session API stays the same.
"""
import json
import time
from dataclasses import dataclass, field

from langchain_core.messages import (AIMessage, HumanMessage, SystemMessage,
                                     convert_to_messages, messages_from_dict,
                                     messages_to_dict)

from app import db
from app.textfmt import money
from app.config import HANDOFF_HOURS, HISTORY_CAP, SESSION_TTL_HOURS


STATE_MSG_NAME = "session_state"   # marks the per-turn state note; never saved


def _now() -> float:
    """Single clock for this module (tests replace it to fake time passing)."""
    return time.time()


@dataclass
class Session:
    phone: str
    messages: list = field(default_factory=list)   # LangChain messages, trimmed
    cart: dict = field(default_factory=dict)       # product_id -> qty (used from Stage 3)
    last_order_key: str | None = None
    handoff_until: float = 0.0                     # epoch seconds; 0 = no handoff
    branch_id: str | None = None                   # CONFIRMED pickup branch
    proposed_branch_id: str | None = None          # offered, waiting for yes/no
    pending_quote: dict | None = None              # itemised quote awaiting the customer's YES/NO
    outbox: str | None = field(default=None, repr=False)   # code-written reply for this turn (never saved)
    branch_just_confirmed: bool = field(default=False, repr=False)   # this turn only (never saved)
    last_active: float = field(default_factory=lambda: _now())

    def __post_init__(self):
        # Repair: a saved session may hold a proposal equal to the confirmed
        # branch (older versions allowed it). Nothing is waiting for a yes/no.
        if self.proposed_branch_id and self.proposed_branch_id == self.branch_id:
            self.proposed_branch_id = None
        if self.pending_quote and self.pending_quote.get("branch_id") != self.branch_id:
            self.pending_quote = None   # a quote only makes sense for the confirmed branch

    # ----- read helpers -----
    @property
    def handoff_active(self) -> bool:
        return self.handoff_until > _now()

    def state_message(self) -> SystemMessage:
        """A short note telling the MODEL what the system already knows, rebuilt
        every turn. The model cannot see code-owned state otherwise, so without
        this it re-asks questions the customer already answered."""
        branch = db.get_branch(self.branch_id) if self.branch_id else None
        proposed = (db.get_branch(self.proposed_branch_id)
                    if self.proposed_branch_id else None)
        lines = ["SYSTEM STATE (authoritative, maintained by the system - not by you):"]
        if branch:
            lines.append(f"- Confirmed pickup branch: {branch['name']} ({branch['address']}).")
            lines.append("  NEVER ask for the city or area again. Go straight to the shelf "
                         "tools (search_products etc.) for whatever the customer asked.")
            if self.branch_just_confirmed:
                lines.append("  The customer JUST confirmed this branch by saying yes. Carry on with "
                             "what they asked before the branch step (see earlier messages) using the "
                             "shelf tools. If they had not asked for anything yet, reply only: "
                             "What would you like to order?")
        else:
            lines.append("- Confirmed pickup branch: NONE.")
            lines.append("  If the customer wants prices or to order, ask for their city or "
                         "area, then call set_pickup_location.")
        if proposed:
            lines.append(f"- Branch waiting for the customer's yes/no: {proposed['name']} "
                         f"({proposed['address']}).")
            lines.append("  The system handles their yes/no itself; you cannot confirm a branch. "
                         "If they name a different place instead, call set_pickup_location with it.")
        q = self.pending_quote
        if q:
            items = ", ".join(f"{i['quantity']} × {i['name']}" for i in q["items"])
            lines.append(f"- A quote is waiting for the customer's YES/NO ({items}; total "
                         f"{money(q['totals']['total'])}). The system already showed it to them "
                         "and places the order itself when they reply YES.")
            lines.append("  If they want changes, call get_price_quote again with the corrected "
                         "items. You cannot place orders; never say an order is placed or reserved.")
        return SystemMessage(content="\n".join(lines), name=STATE_MSG_NAME)

    # ----- state changes: each one saves immediately -----
    def propose_branch(self, branch_id: str | None) -> None:
        if branch_id and branch_id == self.branch_id:
            branch_id = None   # invariant: never wait for a yes/no on the confirmed branch
        self.proposed_branch_id = branch_id
        self.save()

    def lock_branch(self, branch_id: str) -> None:
        self.branch_id = branch_id
        self.proposed_branch_id = None
        self.pending_quote = None   # a quote for the old branch is void
        self.cart.clear()          # a different branch means different prices/stock
        self.save()

    def record_order(self, order_key: str) -> None:
        self.last_order_key = order_key
        self.pending_quote = None   # the quote has been turned into an order
        self.cart.clear()
        self.save()

    # ----- quote + outbox (Stage 3) -----
    def set_quote(self, quote: dict) -> None:
        self.pending_quote = quote
        self.save()

    def clear_quote(self) -> None:
        self.pending_quote = None
        self.save()

    def quote_expired(self) -> bool:
        return bool(self.pending_quote) and _now() > self.pending_quote["expires_at"]

    def branch_awaiting_reply(self) -> bool:
        """True when a bare YES/NO should be read as the answer to a branch proposal."""
        return bool(self.proposed_branch_id)

    def quote_awaiting_reply(self) -> bool:
        """True when a bare YES/NO should be read as the answer to the quote
        (not when a branch yes/no is the open question)."""
        return bool(self.pending_quote) and not self.proposed_branch_id

    def set_outbox(self, text: str) -> None:
        self.outbox = text   # transient: cleared every turn, never saved

    def take_outbox(self) -> str | None:
        text, self.outbox = self.outbox, None
        return text

    def start_handoff(self, hours: float = HANDOFF_HOURS) -> None:
        self.handoff_until = _now() + hours * 3600
        self.save()

    def release_handoff(self) -> None:
        self.handoff_until = 0.0
        self.save()

    def set_history(self, messages: list) -> None:
        """Replace chat history with the end-of-turn result (trimmed) and save."""
        kept = [m for m in convert_to_messages(messages)
                if getattr(m, "name", None) != STATE_MSG_NAME]   # state note is per-turn only
        self.messages = trim_history(kept)
        self.save()

    # ----- persistence -----
    def to_json(self) -> str:
        return json.dumps({
            "messages": messages_to_dict(convert_to_messages(self.messages)),
            "cart": self.cart,
            "last_order_key": self.last_order_key,
            "handoff_until": self.handoff_until,
            "branch_id": self.branch_id,
            "proposed_branch_id": self.proposed_branch_id,
            "pending_quote": self.pending_quote,
            "last_active": self.last_active,
        }, default=str)   # default=str: an odd metadata value must never crash a save

    def save(self) -> None:
        self.last_active = _now()
        db.save_session_json(self.phone, self.to_json())


# ---------- history trimming ----------

def _is_human(m) -> bool:
    return getattr(m, "type", None) == "human" or (
        isinstance(m, dict) and m.get("role") == "user")


def trim_history(messages: list, cap: int = HISTORY_CAP) -> list:
    """Keep about `cap` newest messages, but NEVER start on an AI tool-call or a
    tool result: a tool result whose call was cut off confuses/crashes the model.
    The kept history always starts at a customer (human) message."""
    if len(messages) <= cap:
        return list(messages)
    tail = messages[-cap:]
    for i, m in enumerate(tail):
        if _is_human(m):
            return list(tail[i:])
    for i in range(len(messages) - 1, -1, -1):      # tail was all tool chatter
        if _is_human(messages[i]):
            return list(messages[i:])
    return list(tail)


# ---------- store ----------

_cache: dict[str, Session] = {}


def _expired(last_active: float) -> bool:
    return _now() - last_active > SESSION_TTL_HOURS * 3600


def _load(phone: str) -> Session:
    raw = db.load_session_json(phone)
    if raw:
        try:
            d = json.loads(raw)
            if not _expired(d.get("last_active", 0)):
                return Session(
                    phone=phone,
                    messages=messages_from_dict(d.get("messages", [])),
                    cart=d.get("cart", {}),
                    last_order_key=d.get("last_order_key"),
                    handoff_until=d.get("handoff_until", 0.0),
                    branch_id=d.get("branch_id"),
                    proposed_branch_id=d.get("proposed_branch_id"),
                    pending_quote=d.get("pending_quote"),
                    last_active=d.get("last_active", _now()),
                )
        except Exception as e:   # corrupt row must never lock a customer out
            print(f"[session] unreadable state for {phone}, starting fresh: {e!r}")
        db.delete_session_row(phone)
    return Session(phone=phone)


def get_session(phone: str) -> Session:
    s = _cache.get(phone)
    if s is not None and _expired(s.last_active):
        _cache.pop(phone, None)
        db.delete_session_row(phone)
        s = None
    if s is None:
        s = _cache[phone] = _load(phone)
    return s


def clear(phone: str) -> None:
    _cache.pop(phone, None)
    db.delete_session_row(phone)


def purge_expired() -> int:
    """Housekeeping, call at startup: drop old rows and stale cache entries."""
    for phone in [p for p, s in _cache.items() if _expired(s.last_active)]:
        _cache.pop(phone, None)
    return db.purge_sessions_older_than(SESSION_TTL_HOURS)


def append_message(session: Session, role: str, content: str, cap: int = HISTORY_CAP):
    msg = HumanMessage(content=content) if role == "user" else AIMessage(content=content)
    session.messages.append(msg)
    session.messages = trim_history(session.messages, cap)   # kept in memory; saved at end of turn
