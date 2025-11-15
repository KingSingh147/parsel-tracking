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
# CONFIGURATION
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # example: https://parsel-tracking.onrender.com
PORT = int(os.getenv("PORT", 10000))

MYSPEEDPOST_ENDPOINTS = [
    "https://myspeedpost.com/track?num={}",
    "https://myspeedpost.com/track?number={}",
    "https://myspeedpost.com/track/{}",
    "https://myspeedpost.com/?num={}",
    "https://myspeedpost.com/?awb={}",
    "https://myspeedpost.com/?tracking={}",
]

FETCH_TIMEOUT = 40  # seconds
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).build()


# -----------------------
# Parse myspeedpost HTML
# -----------------------
def parse_myspeedpost_html(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    latest = {}
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "status" in low and i + 1 < len(lines):
            latest["status"] = lines[i + 1]
        if "location" in low and i + 1 < len(lines):
            latest["location"] = lines[i + 1]
        if ("date" in low or "time" in low) and i + 1 < len(lines):
            latest.setdefault("datetime", lines[i + 1])

    history = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols:
                history.append(" • ".join(cols))
    else:
        for ln in lines:
            if any(k in ln.lower() for k in ("delivered", "out for", "received", "bag", "dispatched", "booking", "arrived")):
                history.append(ln)

    if latest or history:
        return {
            "status": latest.get("status"),
            "location": latest.get("location"),
            "datetime": latest.get("datetime"),
            "history": history,
        }
    return None


# -----------------------
# Async fetch
# -----------------------
async def fetch_myspeedpost(tracking: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        for ep in MYSPEEDPOST_ENDPOINTS:
            url = ep.format(tracking)
            try:
                logger.info("Trying endpoint %s", url)
                r = await client.get(url)
                if r.status_code != 200:
                    continue

                # Try HTML
                parsed = parse_myspeedpost_html(r.text)
                if parsed:
                    return parsed

                # JSON fallback
                if "application/json" in r.headers.get("content-type", ""):
                    j = r.json()
                    if isinstance(j, dict):
                        return {
                            "status": j.get("status") or j.get("current_status"),
                            "location": j.get("location") or j.get("current_location"),
                            "datetime": j.get("datetime") or j.get("time"),
                            "history": j.get("events") or j.get("history") or [],
                        }
            except Exception:
                await asyncio.sleep(0.3)
    return None


# -----------------------
# BOT COMMANDS
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send your India Post tracking number to get live updates.")


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.upper().replace(" ", "")
    ack = await update.message.reply_text("🔍 Fetching live India Post tracking...\n⏳ Please wait 20–35 sec.")

    result = await fetch_myspeedpost(tracking)
    if not result:
        await ack.edit_text("❌ Tracking number not found.\nCheck format like: `EZ123456789IN`", parse_mode="Markdown")
        return

    status = result.get("status") or "Unknown"
    location = result.get("location") or "Unknown"
    dt = result.get("datetime") or "Unknown"
    history = result.get("history") or []

    msg = (
        f"📦 *SpeedPost / India Post Tracking*\n\n"
        f"🔹 *Tracking No:* `{tracking}`\n\n"
        f"🔸 *Current Status:* *{status}*\n"
        f"📍 *Location:* {location}\n"
        f"🕒 *Date & Time:* {dt}\n\n"
    )

    if history:
        msg += "📜 *Recent Activity:*\n" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(history[:5])]) + "\n\n"

    msg += "🔎 _If info looks incomplete, try again after a minute._"

    await ack.edit_text(msg, parse_mode="Markdown")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track))


# -----------------------
# LIFE CYCLE: required to avoid crash
# -----------------------
@app.on_event("startup")
async def start_bot():
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info("Webhook set: %s/webhook", WEBHOOK_URL)


@app.on_event("shutdown")
async def stop_bot():
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.post("/webhook")
async def webhook_listener(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def root():
    return {"status": "Bot running"}
