import os
import logging
import asyncio
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://parsel-tracking.onrender.com
PORT = int(os.getenv("PORT", 10000))

# myspeedpost endpoints (variants to try)
MYSPEEDPOST_ENDPOINTS = [
    "https://myspeedpost.com/track?num={}",
    "https://myspeedpost.com/track?number={}",
    "https://myspeedpost.com/track/{}",
    "https://myspeedpost.com/?num={}",
    "https://myspeedpost.com/?awb={}",
    "https://myspeedpost.com/?tracking={}",
]

# timeouts
FETCH_TIMEOUT = 40  # seconds (max wait for external site)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI + Telegram
app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()


# -----------------------
# Utility: parse myspeedpost HTML
# -----------------------
def parse_myspeedpost_html(html: str) -> Optional[dict]:
    """
    Try to extract latest status, location and datetime from myspeedpost HTML.
    Returns dict {status, location, datetime, history(list)} or None if not found.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    latest = {}
    # Heuristics: look for nearby keywords and values
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "status" in low and i + 1 < len(lines):
            latest["status"] = lines[i + 1]
        if "location" in low and i + 1 < len(lines):
            latest["location"] = lines[i + 1]
        if ("date" in low or "time" in low) and i + 1 < len(lines):
            latest.setdefault("datetime", lines[i + 1])

    # Class/id based fallback
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

    # Extract history if present (table rows)
    history = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols and any(c.strip() for c in cols):
                history.append(" • ".join([c for c in cols if c]))
    else:
        # fallback: add lines that look like events
        for ln in lines:
            if any(k in ln.lower() for k in ("delivered", "out for", "received", "bag", "dispatched", "booking", "arrived")):
                history.append(ln)

    if latest or history:
        return {
            "status": latest.get("status"),
            "location": latest.get("location"),
            "datetime": latest.get("datetime"),
            "history": history,
            "raw_text_snippet": "\n".join(lines[:60]),
        }
    return None


# -----------------------
# Async fetch function
# -----------------------
async def fetch_myspeedpost(tracking: str) -> Optional[dict]:
    """
    Try multiple myspeedpost endpoints. Returns parsed dict or None.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        last_exc = None
        for endpoint in MYSPEEDPOST_ENDPOINTS:
            url = endpoint.format(tracking)
            try:
                logger.info("Trying endpoint: %s", url)
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.info("Endpoint %s returned %s", url, resp.status_code)
                    continue
                parsed = parse_myspeedpost_html(resp.text)
                if parsed:
                    logger.info("Parsed result from %s", url)
                    return parsed
                # Try JSON body fallback
                ctype = resp.headers.get("content-type", "")
                if "application/json" in ctype:
                    try:
                        j = resp.json()
                        if isinstance(j, dict):
                            data = {}
                            data["status"] = j.get("status") or j.get("current_status") or j.get("message")
                            data["location"] = j.get("location") or j.get("current_location")
                            data["datetime"] = j.get("datetime") or j.get("time")
                            data["history"] = j.get("history") or j.get("events") or []
                            if data.get("status") or data.get("history"):
                                return data
                    except Exception:
                        pass
                # no useful data, try next
            except Exception as e:
                logger.exception("Error fetching %s: %s", url, e)
                last_exc = e
                await asyncio.sleep(0.3)
        if last_exc:
            logger.warning("All endpoints failed; last exception: %s", last_exc)
    return None


# -----------------------
# Bot handlers (selected styles)
# -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📦 Send your India Post tracking number (e.g. EE123456789IN) and I'll fetch live status (may take ~20–35s)."
    )


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip().upper()
    if not tracking:
        await update.message.reply_text("Please send a tracking number.")
        return

    # Instant acknowledgement (Style 2)
    ack = await update.message.reply_text(
        "📦 Tracking your SpeedPost parcel...\n⏳ Please wait 20–35 seconds while we fetch live India Post data."
    )

    # fetch data (async) - do not block event loop beyond timeout
    try:
        parsed = await fetch_myspeedpost(tracking)
    except Exception as e:
        logger.exception("Unexpected error while fetching: %s", e)
        parsed = None

    # If no result -> Format B invalid message
    if not parsed:
        try:
            await ack.edit_text(
                "❌ Tracking number not found.\n\nMake sure it looks like this: `EZ123456789IN`",
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text(
                "❌ Tracking number not found.\n\nMake sure it looks like this: EZ123456789IN"
            )
        return

    # Build beautiful final message
    status = parsed.get("status") or "Status not available"
    location = parsed.get("location") or "Location not available"
    dt = parsed.get("datetime") or "Date & Time not available"

    # Short history preview (max 5)
    history = parsed.get("history") or []
    history_lines = []
    for i, h in enumerate(history[:5], start=1):
        history_lines.append(f"{i}. {h}")

    pretty_msg = (
        f"📦 *SpeedPost / India Post Tracking*\n\n"
        f"🔹 *Tracking No:* `{tracking}`\n\n"
        f"🔸 *Current Status:* *{status}*\n"
        f"📍 *Location:* {location}\n"
        f"🕒 *Date & Time:* {dt}\n\n"
    )

    if history_lines:
        pretty_msg += "📜 *Recent Activity:*\n" + "\n".join(history_lines) + "\n\n"

    pretty_msg += "🔎 _If details look incomplete, try again after a minute (the server may still be updating)._"

    # Edit ack to final
    try:
        await ack.edit_text(pretty_msg, parse_mode="Markdown")
    except Exception:
        await update.message.reply_markdown(pretty_msg)


# register handlers
telegram_app.add_handler(CommandHandler("start", start_cmd))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_handler))


# -----------------------
# FastAPI lifecycle: init PTB and webhook
# -----------------------
@app.on_event("startup")
async def on_startup():
    # Initialize & start the PTB Application
    await telegram_app.initialize()
    await telegram_app.start()
    # Set webhook if WEBHOOK_URL present
    if WEBHOOK_URL:
        await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
        logger.info("Webhook set to %s/webhook", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_URL not set; webhook not configured.")


@app.on_event("shutdown")
async def on_shutdown():
    await telegram_app.stop()
    await telegram_app.shutdown()


# webhook endpoint for Telegram
@app.post("/webhook")
async def webhook_listener(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def root():
    return {"status": "Bot running via FastAPI webhook"}


# -----------------------
# Local run (uvicorn)
# -----------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
