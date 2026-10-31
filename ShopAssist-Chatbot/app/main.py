"""FastAPI entrypoint: Meta webhook verification + inbound message handling.

Async design note: the webhook answers Meta immediately (HTTP 200, required),
then the agent runs in a background asyncio task so Meta never times out and
burst traffic doesn't block the event loop.
For serious scale SWAP this for a real queue (Celery/RQ/SQS)."""
import asyncio

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.agent import run_agent
from app.config import SWEEP_MINUTES, WHATSAPP_VERIFY_TOKEN
from app.db import init_db, release_expired
from app.session import purge_expired
from app.whatsapp import send_whatsapp

app = FastAPI(title="ShopAssist Bot", docs_url=None, redoc_url=None)


def sweep_once() -> int:
    """Expire reservations whose hold + grace has ended and put their stock back."""
    return release_expired()


async def sweep_forever() -> None:
    """Every SWEEP_MINUTES, so the shelf is right even when nobody is chatting."""
    while True:
        await asyncio.sleep(SWEEP_MINUTES * 60)
        try:
            released = await asyncio.to_thread(sweep_once)
            if released:
                print(f"[sweeper] released {released} expired reservation(s)")
        except Exception as e:                   # the sweeper must never die
            print(f"[sweeper error] {e!r}")


@app.on_event("startup")
async def startup() -> None:
    init_db()         # also upgrades an older database in place
    purge_expired()   # forget chats idle past SESSION_TTL_HOURS
    sweep_once()      # release anything that expired while the server was off
    app.state.sweeper = asyncio.create_task(sweep_forever())


# ---------- Meta webhook verification ----------

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


# ---------- Inbound messages ----------

def _extract(payload: dict):
    """Pull (phone, text) out of Meta's nested webhook payload, or None."""
    try:
        msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        if msg.get("type") != "text":
            return None
        return msg["from"], msg["text"]["body"].strip()
    except (KeyError, IndexError, TypeError):
        return None  # delivery receipts, statuses, media, etc.


@app.post("/webhook")
async def receive_webhook(request: Request):
    extracted = _extract(await request.json())
    if extracted:
        phone, text = extracted
        asyncio.create_task(_handle(phone, text))
    return {"status": "ok"}  # always ACK fast — Meta retries otherwise


async def _handle(phone: str, text: str) -> None:
    try:
        reply = run_agent(phone, text)
    except Exception as e:  # never leave a customer hanging
        reply = ("Sorry, something went wrong on my side. "
                 "A human agent will follow up shortly.")
        print(f"[agent error] {phone}: {e!r}")
    if reply:   # None = a human has this chat, bot stays silent
        await send_whatsapp(phone, reply)
