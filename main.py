# main.py
import os
import logging
import asyncio
from typing import Optional, List, Dict
import httpx
import json
from telegram.ext import MessageHandler, filters


import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, HTTPException

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
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://yourdomain.com
PORT = int(os.getenv("PORT", "10000"))

# Myspeedpost variants (try multiple)
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
telegram_app: Optional[Application] = None  # will be created at startup


# -----------------------
# Parsing helpers
# -----------------------
def parse_myspeedpost_html(html: str) -> Optional[Dict]:
    """
    Try to extract simple status/location/datetime/history from HTML.
    Uses Python builtin parser to avoid lxml dependency.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    latest = {}
    # heuristics by scanning nearby lines
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "status" in low and i + 1 < len(lines):
            latest["status"] = lines[i + 1]
        if "location" in low and i + 1 < len(lines):
            latest["location"] = lines[i + 1]
        if ("date" in low or "time" in low) and i + 1 < len(lines):
            latest.setdefault("datetime", lines[i + 1])

    # class/id fallback
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

    # table/history
    history: List[str] = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols and any(c.strip() for c in cols):
                history.append(" • ".join([c for c in cols if c]))
    else:
        for ln in lines:
            if any(k in ln.lower() for k in ("delivered", "out for", "received", "bag", "dispatched", "booking", "arrived", "scan")):
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

async def track_speedpost(tracking_no: str):
    url = f"https://www.indiapost.gov.in/_layouts/15/IPSAPI/Tracking/TrackConsignment.aspx?consignment={tracking_no}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)

    data = json.loads(response.text)
    if not data.get("consignment"):
        return None

    item = data["consignment"][0]
    return {
        "number": tracking_no,
        "status": item.get("Status", "Not available"),
        "location": item.get("OfficeName", "Not available"),
        "time": item.get("EventDate", "Not available")
    }


async def fetch_myspeedpost(tracking: str) -> Optional[Dict]:
    """
    Attempt each possible endpoint. Return parsed dict or None.
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

                # Try parsing HTML
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
                        logger.exception("JSON parse error from %s", url)
                # else continue to next endpoint
            except Exception as e:
                logger.exception("Error fetching %s: %s", url, e)
                last_exc = e
                await asyncio.sleep(0.3)
        if last_exc:
            logger.warning("All endpoints tried; last exception: %s", last_exc)
    return None


# -----------------------
# Telegram handlers (async)
# -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send your India Post tracking number (e.g. EE123456789IN). I will fetch live status.")


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("Please send a tracking number.")
        return

    tracking = raw.upper().replace(" ", "")
    ack = await update.message.reply_text("🔍 Fetching… please wait 20–35 seconds while I query India Post endpoints.")
    try:
        parsed = await fetch_myspeedpost(tracking)
    except Exception:
        parsed = None

    if not parsed:
        try:
            await ack.edit_text(
                "❌ Tracking number not found.\nMake sure it looks like this: `EZ123456789IN`",
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text("❌ Tracking number not found. Example: EZ123456789IN")
        return

    status = parsed.get("status") or "Status not available"
    location = parsed.get("location") or "Location not available"
    dt = parsed.get("datetime") or "Date & Time not available"

    msg = (
        f"📦 *SpeedPost / India Post Tracking*\n\n"
        f"🔹 *Tracking No:* `{tracking}`\n\n"
        f"🔸 *Current Status:* *{status}*\n"
        f"📍 *Location:* {location}\n"
        f"🕒 *Date & Time:* {dt}\n\n"
    )

    history = parsed.get("history") or []
    if history:
        # max 6 lines
        msg += "📜 *Recent Activity:*\n" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(history[:6])]) + "\n\n"

    msg += "🔎 _If details look incomplete, try again after a minute (India Post may still be updating)._"

    try:
        await ack.edit_text(msg, parse_mode="Markdown")
    except Exception:
        await update.message.reply_markdown(msg)

async def handle_tracking(update, context):
    tracking_no = update.message.text.strip()

    if len(tracking_no) < 8:
        await update.message.reply_text("❌ Please enter a valid tracking number.")
        return

    await update.message.reply_text("⏳ Fetching live tracking from India Post…")

    result = await track_speedpost(tracking_no)

    if not result:
        await update.message.reply_text("❌ Invalid tracking number or India Post server busy. Try again after 1 minute.")
        return

    reply = (
        "📦 *SpeedPost / India Post Tracking*\n\n"
        f"🔹 *Tracking No:* `{result['number']}`\n\n"
        f"🔸 *Current Status:* {result['status']}\n"
        f"📍 *Location:* {result['location']}\n"
        f"🕒 *Date & Time:* {result['time']}\n"
    )
    await update.message.reply_markdown(reply)


# -----------------------
# FastAPI lifecycle - create Telegram Application here
# -----------------------
@app.on_event("startup")
async def startup_event():
    global telegram_app
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment")
        raise RuntimeError("BOT_TOKEN environment variable is required")

    # build the Application
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    # register handlers
    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tracking))


    # initialize & start
    await telegram_app.initialize()
    await telegram_app.start()

    # webhook set if provided
    if WEBHOOK_URL:
        webhook_target = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await telegram_app.bot.set_webhook(webhook_target)
        logger.info("Webhook set to %s", webhook_target)
    else:
        logger.info("WEBHOOK_URL not set — webhook disabled. Using webhooks is recommended on hosted environments.")


@app.on_event("shutdown")
async def shutdown_event():
    global telegram_app
    if telegram_app:
        try:
            await telegram_app.stop()
            await telegram_app.shutdown()
        except Exception as e:
            logger.exception("Error shutting down telegram app: %s", e)


# webhook endpoint for Telegram
@app.post("/webhook")
async def webhook_listener(request: Request):
    global telegram_app
    if telegram_app is None:
        raise HTTPException(status_code=503, detail="telegram app not ready")
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def root():
    return {"status": "ok", "bot": bool(telegram_app is not None)}
