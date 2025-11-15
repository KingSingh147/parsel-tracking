import os
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = "https://parsel-tracking.onrender.com/webhook"

INDIAN_TRACKING_API = "https://indiantracking.in/api/track?courier=india-post&awb="

app = Flask(__name__)
telegram_app = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send any India Post tracking number to get live parcel status.")


async def track_parcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip()
    response = requests.get(INDIAN_TRACKING_API + tracking).json()

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
    json_update = request.get_json(force=True)
    telegram_app.update_queue.put(json_update)
    return "ok"


@app.get("/")
def home():
    return "Bot is running 🚀"


async def set_webhook(app):
    await app.bot.set_webhook(WEBHOOK_URL)


def main():
    global telegram_app
    telegram_app = Application.builder().token(TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_parcel))

    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path="/webhook",
        webhook_url=WEBHOOK_URL
    )


if __name__ == "__main__":
    main()
