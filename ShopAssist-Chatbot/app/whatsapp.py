"""Meta WhatsApp Cloud API sender.

DEV MODE: if WHATSAPP_TOKEN is empty, replies are printed to the console —
useful for running eval/test_queries.py or local webhook tests without Meta.
"""
import asyncio

import httpx

from app.config import (META_API_VERSION, WHATSAPP_PHONE_NUMBER_ID,
                        WHATSAPP_TOKEN)

_GRAPH = (f"https://graph.facebook.com/{META_API_VERSION}/"
          f"{WHATSAPP_PHONE_NUMBER_ID}/messages")


def chunk(text: str, limit: int = 900) -> list[str]:
    """Split a long reply into WhatsApp-friendly bubbles on paragraph/line
    boundaries, never mid-word."""
    parts, buf = [], ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > limit:
            if buf:
                parts.append(buf.strip())
            while len(para) > limit:
                cut = para.rfind(" ", 0, limit)
                parts.append(para[:cut if cut > 0 else limit])
                para = para[(cut if cut > 0 else limit):].lstrip()
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf.strip():
        parts.append(buf.strip())
    return parts or [""]


async def send_whatsapp(to: str, text: str) -> None:
    if not WHATSAPP_TOKEN:
        print(f"\n[DEV whatsapp -> {to}]\n{text}\n{'-' * 40}")
        return
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for part in chunk(text):
            await client.post(_GRAPH, headers=headers, json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": part},
            })
            await asyncio.sleep(0.4)  # polite pacing, keeps message order
