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

# endpoints to try (myspeedpost variants)
MYSPEEDPOST_ENDPOINTS = [
    "https://myspeedpost.com/track?num={}",
    "https://myspeedpost.com/track?number={}",
    "https://myspeedpost.com/track/{}",
    "https://myspeedpost.com/?num={}",
    "https://myspeedpost.com/?awb={}",
    "https://myspeedpost.com/?tracking={}",
]

# timeouts
FETCH_TIMEOUT = 40  # seconds

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
    We'll look for common keywords and table/rows.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(separator="\n").strip()

    # quick heuristics first: look for common words
    # 1) Find lines that contain words like "Status", "Location", "Date", "Time", "Delivered", "Out for"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # try to find a "latest" block by scanning lines for keywords
    latest = {}
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "status" in low and i + 1 < len(lines):
            latest['status'] = lines[i + 1]
        if "location" in low and i + 1 < len(lines):
            latest['location'] = lines[i + 1]
        if ("date" in low or "time" in low) and i + 1 < len(lines):
            # combine date/time if possible
            latest['datetime'] = lines[i + 1]

    # If that failed, attempt more targeted searches in soup
    if not latest.get('status'):
        # look for elements with 'status' in class or id
        el = soup.find(class_=lambda c: c and "status" in c.lower()) or soup.find(id=lambda i: i and "status" in i.lower())
        if el:
            latest['status'] = el.get_text(strip=True)

    if not latest.get('location'):
        el = soup.find(class_=lambda c: c and "location" in c.lower()) or soup.find(id=lambda i: i and "location" in i.lower())
        if el:
            latest['location'] = el.get_text(strip=True)

    if not latest.get('datetime'):
        el = soup.find(class_=lambda c: c and ("date" in c.lower() or "time" in c.lower()))
        if el:
            latest['datetime'] = el.get_text(strip=True)

    # Attempt to extract history rows if available (table or list)
    history = []
    table = soup.find("table")
    if table:
        for tr in table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols:
                history.append(" • ".join(cols))
    else:
        # fallback: find recurring patterns like "DD Mon YYYY - Location - Status"
        for ln in lines:
            if any(k in ln.lower() for k in ("delivered", "out for", "received", "bag", "dispatched", "booking", "arrived")):
                history.append(ln)

    # If we have at least one piece of data, return
    if latest or history:
        return {
            "status": latest.get("status"),
            "location": latest.get("location"),
            "datetime": latest.get("datetime"),
            "history": history,
            "raw_text_snippet": "\n".join(lines[:40])  # small snippet fallback
        }
    return None


# -----------------------
# Async fetch function
# -----------------------
async def fetch_myspeedpost(tracking: str) -> Optional[dict]:
    """
    Try multiple myspeedpost endpoints (some sites use different params).
    This waits up to FETCH_TIMEOUT seconds for a response.
    Returns parsed dict or None.
    """
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        last_exc = None
        for endpoint in MYSPEEDPOST_ENDPOINTS:
            url = endpoint.format(tracking)
            try:
                logger.info("Trying myspeedpost endpoint: %s", url)
                resp = await client.get(url)
                # many sites render content with JS, but myspeedpost worked for you in browser
                if resp.status_code != 200:
                    logger.info("Endpoint %s returned status %s", url, resp.status_code)
                    continue
                parsed = parse_myspeedpost_html(resp.text)
                if parsed:
                    logger.info("Parsed myspeedpost successfully from %s", url)
                    return parsed
                # fallback: sometimes the page contains JSON within scripts
                if "application/json" in resp.headers.get("content-type", ""):
                    try:
                        j = resp.json()
                        # try to find keys
                        data = {}
                        if isinstance(j, dict):
                            # naive extraction
                            data['status'] = j.get('status') or j.get('current_status') or j.get('message')
                            data['location'] = j.get('location') or j.get('current_location')
                            data['datetime'] = j.get('datetime') or j.get('time')
                            data['history'] = j.get('history') or j.get('events') or []
                            if data['status'] or data['history']:
                                return data
                    except Exception:
                        pass
                # otherwise continue to next endpoint
            except Exception as e:
                logger.exception("Error fetching %s: %s", url, e)
                last_exc = e
                # try next endpoint
                await asyncio.sleep(0.3)
        # nothing found
        if last_exc:
            logger.warning("All myspeedpost endpoints failed; last exception: %s", last_exc)
    return None


# -----------------------
# Bot handlers
# -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send your India Post tracking number (e.g. EE123456789IN) and I'll fetch the latest status.")


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip().upper()
    if not tracking:
        await update.message.reply_text("Please send a tracking number.")
        return

    # Acknowledge and tell user we may take time
    ack = await update.message.reply_text(
        "⏳ Tracking started — this can take up to 35 seconds. Please wait..."
    )

    # fetch the tracking info (async)
    try:
        parsed = await fetch_myspeedpost(tracking)
    except Exception as e:
        logger.exception("Unexpected error during tracking fetch: %s", e)
        parsed = None

    if not parsed:
        await ack.edit_text(
            "❌ Could not retrieve tracking details from myspeedpost. The service might be slow or unavailable.\n\n"
            "You can try again, or open the official site: https://www.indiapost.gov.in/"
        )
        return

    # Build a beautiful message (B: last update + location + time)
    status = parsed.get("status") or "Status not available"
    location = parsed.get("location") or "Location not available"
    dt = parsed.get("datetime") or "Date/Time not available"

    # Short history preview (up to 4 items)
    history = parsed.get("history") or []
    history_lines = []
    for i, h in enumerate(history[:4], start=1):
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

    # edit the acknowledgment to final message
    try:
        await ack.edit_text(pretty_msg, parse_mode="Markdown")
    except Exception:
        # fallback to simple reply if edit fails
        await update.message.reply_markdown(pretty_msg)


# register handlers
telegram_app.add_handler(CommandHandler("start", start_cmd))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_handler))


# -----------------------
# FastAPI lifecycle: init PTB and webhook
# -----------------------
@app.on_event("startup")
async def on_startup():
    # initialize and start telegram application properly
    await telegram_app.initialize()
    await telegram_app.start()
    # set webhook
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
# Local run guard (uvicorn)
# -----------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
