import os
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://parsel-tracking.onrender.com

INDIAN_TRACKING_API = "https://indiantracking.in/api/track?courier=india-post&awb="

app = Flask(__name__)
telegram_app = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📮 Send an India Post tracking number to get live parcel status."
    )


async def track_parcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip()
    api = INDIAN_TRACKING_API + tracking

    try:
        r = requests.get(api, timeout=10).json()
    except:
        await update.message.reply_text("⚠ Tracking server offline. Try later.")
        return

    if not r.get("success"):
        await update.message.reply_text("❌ Tracking number not found.")
        return

    d = r.get("data", {})
    status = d.get("current_status", "Not available")
    location = d.get("current_location", "Not available")
    dt = d.get("current_datetime", "Not available")

    msg = f"""📦 *India Post Tracking*

🟢 *Status:* {status}
📍 *Location:* {location}
🕒 *Date/Time:* {dt}

🔍 *Tracking:* `{tracking}`
"""
    await update.message.reply_markdown(msg)


@app.post("/webhook")
def webhook():
    upd = request.get_json()
    telegram_app.update_queue.put_nowait(upd)
    return "ok"


@app.get("/")
def root():
    return "Bot is running 🚀"


def main():
    global telegram_app

    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_parcel))

    # Set webhook before starting
    asyncio.get_event_loop().run_until_complete(
        telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    )

    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"{WEBHOOK_URL}/webhook"
    )


if __name__ == "__main__":
    main()
