import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def track_india_post(tracking_number):
    api_url = "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/TrackConsignment.aspx/GetConsignmentDetails"
    headers = {"Content-Type": "application/json"}
    data = {"barCode": tracking_number}

    res = requests.post(api_url, json=data, headers=headers)
    result = res.json()

    if not result or "d" not in result or not result["d"]:
        return f"❌ Tracking number *{tracking_number}* not found."

    last_event = result["d"][0]  # latest update

    status = last_event.get("Event", "Status unavailable")
    location = last_event.get("Office", "Location unavailable")
    date = last_event.get("EventDate", "")
    time = last_event.get("EventTime", "")
    datetime = f"{date} {time}".strip()

    return f"""
📦 *India Post Tracking Update*

🟢 *Status:* {status}
📍 *Location:* {location}
🕒 *Time:* {datetime}

🔍 Tracking: `{tracking_number}`
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📮 Send an India Post tracking number to get live status.")

async def handle_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tracking_number = update.message.text.strip()
    await update.message.reply_text("⏳ Fetching live data... please wait...")

    result = track_india_post(tracking_number)
    await update.message.reply_markdown(result)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tracking))
    app.run_polling()

if __name__ == "__main__":
    main()
