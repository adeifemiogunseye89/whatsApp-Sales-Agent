"""Per-phone conversation state.

SWAP FOR PRODUCTION: this in-memory dict dies on restart and isn't shared
across processes. Replace with Redis (hash per phone, TTL e.g. 24h) using
the exact same interface.
"""
from dataclasses import dataclass, field


@dataclass
class Session:
    phone: str
    messages: list = field(default_factory=list)   # capped LLM chat history
    cart: dict = field(default_factory=dict)       # product_id -> qty
    last_order_key: str | None = None
    handoff: bool = False
    branch_id: str | None = None            # CONFIRMED branch
proposed_branch_id: str | None = None   # offered, waiting for yes/no


_store: dict[str, Session] = {}


def get_session(phone: str) -> Session:
    if phone not in _store:
        _store[phone] = Session(phone=phone)
    return _store[phone]


def clear(phone: str) -> None:
    _store.pop(phone, None)


def append_message(session: Session, role: str, content: str, cap: int = 20):
    session.messages.append({"role": role, "content": content})
    if len(session.messages) > cap:          # keep context small + cheap
        session.messages = session.messages[-cap:]
