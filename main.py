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
# CONFIG
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://parsel-tracking.onrender.com
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN is missing — set it in Render Environment Variables")

if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL is missing — set it in Render Environment Variables")

app = FastAPI()
telegram_app = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MYSPEEDPOST_ENDPOINTS = [
    "https://myspeedpost.com/track?num={}",
    "https://myspeedpost.com/track?number={}",
    "https://myspeedpost.com/track/{}",
    "https://myspeedpost.com/?num={}",
    "https://myspeedpost.com/?awb={}",
    "https://myspeedpost.com/?tracking={}",
]

FETCH_TIMEOUT = 40


# -----------------------
# PARSER
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
        return {**latest, "history": history}
    return None


# -----------------------
# FETCH
# -----------------------
async def fetch_myspeedpost(tracking: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        for ep in MYSPEEDPOST_ENDPOINTS:
            try:
                r = await client.get(ep.format(tracking))
                if r.status_code != 200:
                    continue

                parsed = parse_myspeedpost_html(r.text)
                if parsed:
                    return parsed

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
# BOT HANDLERS
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send your tracking number to get India Post live updates.")


async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.upper().replace(" ", "")
    ack = await update.message.reply_text("🔍 Fetching India Post tracking...\n⏳ 20–35 sec...")

    result = await fetch_myspeedpost(tracking)
    if not result:
        await ack.edit_text("❌ Invalid tracking number.\nFormat: `EZ123456789IN`", parse_mode="Markdown")
        return

    status = result.get("status", "Unknown")
    location = result.get("location", "Unknown")
    dt = result.get("datetime", "Unknown")
    hist = result.get("history", [])

    msg = (
        f"📦 *SpeedPost Tracking*\n\n"
        f"🔹 *Tracking:* `{tracking}`\n"
        f"🔸 *Status:* *{status}*\n"
        f"📍 *Location:* {location}\n"
        f"🕒 *Updated:* {dt}\n\n"
    )

    if hist:
        msg += "📜 *Recent Activity:*\n" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(hist[:5])]) + "\n\n"

    msg += "🔎 _If incomplete, retry after 1 min._"

    await ack.edit_text(msg, parse_mode="Markdown")


# -----------------------
# BOOT / WEBHOOK
# -----------------------
@app.on_event("startup")
async def startup():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track))

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    logger.info("Webhook active")


@app.on_event("shutdown")
async def shutdown():
    await telegram_app.stop()
    await telegram_app.shutdown()


@app.post("/webhook")
async def webhook_listener(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def home():
    return {"status": "Bot Running ✔️"}
