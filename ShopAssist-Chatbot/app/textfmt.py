"""Text helpers shared by tools, orders, session and agent (Stage 3).

Kept free of app imports (except config) so every other module can use it
without creating an import cycle.
"""
import re
from datetime import datetime, timedelta, timezone

from app.config import CURRENCY

WAT = timezone(timedelta(hours=1))   # Nigeria: UTC+1, no daylight saving


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
