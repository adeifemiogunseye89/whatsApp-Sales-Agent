"""Text helpers shared by tools, orders, session and agent (Stage 3).

Kept free of app imports (except config) so every other module can use it
without creating an import cycle.
"""
import re
from datetime import datetime, timedelta, timezone

from app.config import CURRENCY

WAT = timezone(timedelta(hours=1))   # Nigeria: UTC+1, no daylight saving

# Models (and phones) write look-alike hyphens: U+2010 to U+2015, the minus sign and friends.
_DASH_CHARS = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_DASHES = dict.fromkeys(map(ord, _DASH_CHARS), "-")
_ORD_DASH_RE = re.compile("\\bORD[" + _DASH_CHARS + "]", re.I)


def normalize_order_id(raw: str) -> str:
    """"ord 1b71...", "ORD<look-alike hyphen>1B71...", " ORD-1b71... " -> "ORD-1B71..."."""
    s = (raw or "").translate(_DASHES).strip().upper()
    m = re.fullmatch(r"ORD[\s_-]*([A-Z0-9]{4,})", s)
    return f"ORD-{m.group(1)}" if m else s


def fix_order_dashes(text: str) -> str:
    """Make the hyphen right after 'ORD' a plain one. Customers copy IDs from the bot's
    messages, and our reality checks look for a plain hyphen."""
    return _ORD_DASH_RE.sub("ORD-", text)


def hours_phrase(n: int) -> str:
    return f"{n} hour" if n == 1 else f"{n} hours"


def money(kobo: int) -> str:
    """12345600 -> '₦123,456'   |   1050 -> '₦10.50'   (integer math only)"""
    naira, rem = divmod(kobo, 100)
    return f"{CURRENCY}{naira:,}" + (f".{rem:02d}" if rem else "")


def wat(utc_text: str) -> str:
    """DB stores UTC text; customers see Lagos time."""
    dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(WAT).strftime("%d %b %Y, %I:%M %p")


def to_whatsapp(text: str) -> str:
    """Models write Markdown; WhatsApp bolds with ONE asterisk.
    '**Order ID:**' would show literal asterisks to the customer."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text, flags=re.S)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.M)
    return text
