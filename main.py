import os
from fastapi import FastAPI, Request
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
import httpx
import asyncio

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
bot = Bot(token=BOT_TOKEN)

app = FastAPI()
dispatcher = Dispatcher(bot, None, workers=4, use_context=True)


async def start(update, context):
    await update.message.reply_text("Bot running successfully 🚀")


async def echo(update, context):
    await update.message.reply_text(f"You said: {update.message.text}")


dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))


@app.on_event("startup")
async def startup():
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot)
    dispatcher.process_update(update)
    return {"ok": True}


@app.get("/")
async def home():
    return {"status": "Bot Running ✔"}
