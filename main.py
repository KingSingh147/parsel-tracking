from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
import asyncio
import httpx
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # your render domain + /webhook

app = FastAPI()
telegram_app = None

async def start_command(update: Update, context):
    await update.message.reply_text("Bot is working successfully ✔")

async def echo(update: Update, context):
    text = update.message.text
    await update.message.reply_text(f"You said: {text}")

@app.on_event("startup")
async def startup_event():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(url=WEBHOOK_URL)
    await telegram_app.start()

@app.on_event("shutdown")
async def shutdown_event():
    global telegram_app
    if telegram_app is not None:
        await telegram_app.stop()
        await telegram_app.shutdown()

@app.post("/webhook")
async def webhook_handler(request: Request):
    global telegram_app
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

@app.get("/")
async def home():
    return {"status": "Bot Running 🚀"}
