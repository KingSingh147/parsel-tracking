import os
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def track_india_post(tracking_number):
    url = f"https://www.aftership.com/couriers/india-post?tracking-numbers={tracking_number}"
    response = requests.get(url)

    soup = BeautifulSoup(response.text, "html.parser")

    status = soup.find("span", class_="status-text")
    location = soup.find("span", class_="tracking-location")
    datetime = soup.find("span", class_="tracking-date")

    status = status.text.strip() if status else "Status not found"
    location = location.text.strip() if location else "Location not available"
    datetime = datetime.text.strip() if datetime else "Date/Time not available"

    return f"""
📦 *India Post Tracking*

🟢 *Status:* {status}
📍 *Location:* {location}
🕒 *Time:* {datetime}

🔍 Tracking: `{tracking_number}`
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📮 Send any India Post tracking number to get live parcel status.")

async def handle_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking_number = update.message.text.strip()
    await update.message.reply_text("⏳ Tracking your parcel... please wait...")

    result = track_india_post(tracking_number)
    await update.message.reply_markdown(result)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tracking))
    app.run_polling()

if __name__ == "__main__":
    main()
