from fastapi import FastAPI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import os

TOKEN = os.getenv("BOT_TOKEN")

app = FastAPI()

# Telegram bot
bot_app = ApplicationBuilder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is working! 🚀")

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tracking feature coming soon 🔍")

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track))


@app.get("/")
async def root():
    return {"status": "running"}


@app.post("/webhook")
async def webhook(update: dict):
    telegram_update = Update.de_json(update, bot_app.bot)
    await bot_app.process_update(telegram_update)
    return {"ok": True}
