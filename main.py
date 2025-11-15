import os
import logging
import requests
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import uvicorn

# -----------------------------
# Environment Variables
# -----------------------------
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Example: https://parsel-tracking.onrender.com
INDIAN_TRACKING_API = "https://indiantracking.in/api/track?courier=india-post&awb="

logging.basicConfig(level=logging.INFO)

# FastAPI app
app = FastAPI()

# Telegram Bot
telegram_app = (
    Application.builder()
    .token(TOKEN)
    .build()
)

# -----------------------------
# Bot Handlers
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Send any India Post tracking number to check parcel status.")


async def track_parcel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking = update.message.text.strip()
    response = requests.get(INDIAN_TRACKING_API + tracking).json()

    if not response.get("success"):
        await update.message.reply_text("❌ Tracking number not found.")
        return

    data = response.get("data", {})
    msg = f"""📦 *India Post Tracking*

🟢 *Status:* {data.get('current_status', 'Not available')}
📍 *Location:* {data.get('current_location', 'Not available')}
🕒 *Date/Time:* {data.get('current_datetime', 'Not available')}

🔍 *Tracking:* `{tracking}`
"""
    await update.message.reply_markdown(msg)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_parcel))

# -----------------------------
# Webhook Setup
# -----------------------------
@app.on_event("startup")
async def startup():
    if WEBHOOK_URL:
        await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
        print("🔗 Webhook connected!")


@app.post("/webhook")
async def webhook_listener(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def home():
    return {"Bot": "Running via FastAPI Webhook 🚀"}


# -----------------------------
# Start Uvicorn on Render
# -----------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
