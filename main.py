# main.py
import os
import logging
import asyncio
from typing import Optional, List

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -----------------------
# Config / env
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.onrender.com
PORT = int(os.getenv("PORT", 10000))

# myspeedpost endpoints to try
MYSPEEDPOST_ENDPOINTS = [
    "https://myspeedpost.com/track?num={}",
    "https://myspeedpost.com/track?number={}",
    "https://myspeedpost.com/track/{}",
    "https://myspeedpost.com/?num={}",
    "https://myspeedpost.com/?awb={}",
    "https://myspeedpost.com/?tracking={}",
]

# HTTP fetch timeout
FETCH_TIMEOUT = 40  # seconds

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


# -----------------------
# Parsing helper
# -----------------------
def parse_myspeedpost_html(html: str) -> Optional[dict]:
    """
    Extract status, location, datetime and history from myspeedpost HTML.
    Returns dict or None.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    latest = {}
    # simple heuristics scanning neighbouring lines
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "status" in low and i + 1 < len(lines):
            latest["status"] = lines[i + 1]
        if "location" in low and i + 1 < len(lines):
            latest["location"] = lines[i + 1]
        if ("date" in low or "time" in low) and i + 1 < len(lines):
            latest.setdefault("datetime", lines[i + 1])

    # fallback class/id search
    if not latest.get("status"):
        el = soup.find(class_=lambda c: c and "status" in c.lower()) or soup.find(id=lambda i: i and "status" in i.lower())
        if el:
            latest["status"] = el.get_text(strip=True)

    if not latest.get("location"):
        el = soup.find(class_=lambda c: c and "location" in c.lower()) or soup.find(id=lambda i: i and "location" in i.lower())
        if el:
            latest["location"] = el.get_text(strip=True)

    if not latest.get("datetime"):
        el = soup.find(class_=lambda c: c and ("date" in c.lower() or "time" in c.lower()))
        if el:
            latest["datetime"] = el.get_text(strip=True)

    history: List[str] = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols and any(c for c in cols):
                history.append(" • ".join([c for c in cols if c]))
    else:
        # fallback: lines that look like events
        for ln in lines:
            if any(k in ln.lower() for k in ("delivered", "out for", "received", "bag", "dispatched", "booking", "arrived", "delivery")):
                history.append(ln)

    if latest or history:
        return {
            "status": latest.get("status"),
            "location": latest.get("location"),
            "datetime": latest.get("datetime"),
            "history": history,
            "raw_snippet": "\n".join(lines[:60]),
        }
    return None


# -----------------------
# Async fetch function
# -----------------------
async def fetch_myspeedpost(tracking: str) -> Optional[dict]:
    """
    Try multiple myspeedpost endpoints. Returns parsed dict or None.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ParSelBot/1.0)"}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True, headers=headers) as client:
        last_exc = None
        for endpoint in MYSPEEDPOST_ENDPOINTS:
            url = endpoint.format(tracking)
            try:
                logger.info("Trying endpoint: %s", url)
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.info("Endpoint %s returned status %s", url, resp.status_code)
                    continue

                # attempt HTML parsing
                parsed = parse_myspeedpost_html(resp.text)
                if parsed:
                    logger.info("Parsed HTML from %s", url)
                    return parsed

                # JSON fallback
                ctype = resp.headers.get("content-type", "")
                if "application/json" in ctype:
                    try:
                        j = resp.json()
                        if isinstance(j, dict):
                            data = {
                                "status": j.get("status") or j.get("current_status") or j.get("message"),
                                "location": j.get("location") or j.get("current_location"),
                                "datetime": j.get("datetime") or j.get("time"),
                                "history": j.get("history") or j.get("events") or [],
                            }
                            if data.get("status") or data.get("history"):
                                return data
                    except Exception:
                        logger.exception("JSON parse error for %s", url)
                # no useful data -> next
            except Exception as e:
                logger.exception("Error fetching %s: %s", url, e)
                last_exc = e
                # small delay between tries
                await asyncio.sleep(0.25)
        if last_exc:
            logger.warning("All endpoints failed; last exception: %s", last_exc)
    return None


# -----------------------
# Telegram handlers
# -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send your India Post tracking number (e.g. EZ123456789IN) and I'll fetch live status.")


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().upper().replace(" ", "")
    if not text:
        await update.message.reply_text("Please send a tracking number like: EZ123456789IN")
        return

    ack = await update.message.reply_text("📦 Tracking your SpeedPost parcel...\n⏳ Please wait up to 40 seconds while we fetch live India Post data.")
    try:
        parsed = await fetch_myspeedpost(text)
    except Exception as e:
        logger.exception("Unexpected error while fetching: %s", e)
        parsed = None

    if not parsed:
        # edit ack (if possible) else send new message
        try:
            await ack.edit_text("❌ Tracking number not found.\n\nMake sure it looks like this: `EZ123456789IN`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Tracking number not found. Make sure it looks like this: EZ123456789IN")
        return

    status = parsed.get("status") or "Status not available"
    location = parsed.get("location") or "Location not available"
    dt = parsed.get("datetime") or "Date & Time not available"
    history = parsed.get("history") or []

    history_preview = ""
    if history:
        history_preview = "\n📜 *Recent Activity:*\n" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(history[:6])]) + "\n\n"

    pretty_msg = (
        f"📦 *SpeedPost / India Post Tracking*\n\n"
        f"🔹 *Tracking No:* `{text}`\n\n"
        f"🔸 *Current Status:* *{status}*\n"
        f"📍 *Location:* {location}\n"
        f"🕒 *Date & Time:* {dt}\n\n"
        f"{history_preview}"
        f"🔎 _If details look incomplete, try again after a minute (server may still be updating)._"
    )

    try:
        await ack.edit_text(pretty_msg, parse_mode="Markdown")
    except Exception:
        await update.message.reply_markdown(pretty_msg)


# -----------------------
# FastAPI lifecycle: build/start/stop Telegram Application during startup/shutdown
# -----------------------
@app.on_event("startup")
async def startup_event():
    # sanity checks
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set - cannot start Telegram bot")
        # we let FastAPI continue starting, but the webhook won't be set
        return

    # Build the Application here (not at import time)
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    # register handlers
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_handler))

    # initialize & start
    await telegram_app.initialize()
    await telegram_app.start()

    # set webhook if URL provided
    if WEBHOOK_URL:
        webhook_target = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        try:
            await telegram_app.bot.set_webhook(webhook_target)
            logger.info("Webhook set to %s", webhook_target)
        except Exception:
            logger.exception("Failed to set webhook to %s", webhook_target)

    # store on app.state for access in webhook endpoint / shutdown
    app.state.telegram_app = telegram_app
    logger.info("Telegram Application initialized and started.")


@app.on_event("shutdown")
async def shutdown_event():
    telegram_app = getattr(app.state, "telegram_app", None)
    if not telegram_app:
        return
    try:
        # clear webhook (best-effort)
        if WEBHOOK_URL:
            await telegram_app.bot.delete_webhook()
            logger.info("Webhook deleted.")
    except Exception:
        logger.exception("Error deleting webhook on shutdown.")
    # stop & shutdown the PTB application
    try:
        await telegram_app.stop()
        await telegram_app.shutdown()
        logger.info("Telegram Application stopped and shutdown complete.")
    except Exception:
        logger.exception("Error stopping telegram application.")


# -----------------------
# Webhook endpoint for Telegram
# -----------------------
@app.post("/webhook")
async def webhook_listener(request: Request):
    if not hasattr(app.state, "telegram_app"):
        # App not initialized with a Telegram Application
        raise HTTPException(status_code=503, detail="Telegram bot not initialized")

    data = await request.json()
    telegram_app: Application = app.state.telegram_app
    update = Update.de_json(data, telegram_app.bot)
    # process update on PTB app (non-blocking)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})


# health
@app.get("/")
async def root():
    return {"status": "Bot running (webhook mode)"}


# local run
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
