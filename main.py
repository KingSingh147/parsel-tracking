import os
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # example: https://parsel-tracking.onrender.com

INDIAN_TRACKING_API = "https://indiantracking.in/api/track?courier=india-post&awb="

app = Flask(__name__)
telegram_app = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send any India Post tracking number to check parcel status.")


async def track_parcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip()
    api_url = INDIAN_TRACKING_API + tracking

    try:
        response = requests.get(api_url, timeout=10).json()
    except:
        await update.message.reply_text("⚠ Error connecting to tracking server. Try later.")
        return

    if response.get("success") is False:
        await update.message.reply_text("❌ Tracking number not found.")
        return

    data = response.get("data", {})
    status = data.get("current_status", "Not available")
    location = data.get("current_location", "Not available")
    datetime = data.get("current_datetime", "Not available")

    msg = f"""📦 *India Post Tracking*

🟢 *Status:* {status}
📍 *Location:* {location}
🕒 *Date/Time:* {datetime}

🔍 *Tracking:* `{tracking}`
"""
    await update.message.reply_markdown(msg)


@app.post("/webhook")
def webhook():
    update = request.get_json()
    telegram_app.update_queue.put_nowait(update)
    return "ok"


@app.get("/")
def home():
    return "Bot is running 🚀"


def main():
    global telegram_app

    telegram_app = Application.builder().token(TOKEN).build()

    # handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_parcel))

    # set webhook before starting run_webhook
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
