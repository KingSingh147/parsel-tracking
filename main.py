import os
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = "https://parsel-tracking.onrender.com/webhook"

INDIAN_TRACKING_API = "https://indiantracking.in/api/track?courier=india-post&awb="

app = Flask(__name__)
bot_app = None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send any India Post tracking number to check parcel status.")


async def track_parcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip()

    # Call the tracking API
    api_url = INDIAN_TRACKING_API + tracking
    response = requests.get(api_url).json()

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
    data = request.get_json()
    bot_app.update_queue.put(data)
    return "ok", 200


@app.get("/")
def home():
    return "Bot is running via webhook 🚀"


async def set_webhook(application):
    await application.bot.set_webhook(url=WEBHOOK_URL)


def main():
    global bot_app
    application = Application.builder().token(TOKEN).build()

    # Telegram handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_parcel))

    bot_app = application
    application.create_task(set_webhook(application))

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


if __name__ == "__main__":
    main()
