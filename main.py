import os
import re
import logging
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from telegram.ext import Updater, MessageHandler, Filters, CommandHandler

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "8599385484:AAHWUXw0JqW9c0i2ztfc1NZjqoJsVYWtgds"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)


def parse_india_post_html(html):
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("#ContentPlaceHolder1_gvTrackingEvents") or soup.select_one("table")
    if not table:
        return None

    rows = []
    for tr in table.select("tr"):
        cols = [td.get_text(strip=True) for td in tr.select("td")]
        if cols:
            rows.append(cols)

    if not rows:
        return None

    first = rows[0]
    if len(first) >= 3:
        return {
            "date_time": first[0],
            "location": first[1],
            "status": " | ".join(first[2:]),
            "rows": rows
        }
    return {"raw_row": " | ".join(first), "rows": rows}


def track_awb(awb):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    url = "https://www.indiapost.gov.in/_layouts/15/dop.portal.tracking/trackconsignment.aspx"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
    except:
        return "❌ Unable to reach India Post server. Try again later."

    soup = BeautifulSoup(r.text, "lxml")
    viewstate = soup.find("input", {"id": "__VIEWSTATE"})
    eventvalidation = soup.find("input", {"id": "__EVENTVALIDATION"})

    data = {"txtConsignment": awb, "btnSearch": "Search"}
    if viewstate and eventvalidation:
        data["__VIEWSTATE"] = viewstate.get("value", "")
        data["__EVENTVALIDATION"] = eventvalidation.get("value", "")

    try:
        post = session.post(url, data=data, timeout=20)
        post.raise_for_status()
    except:
        return "❌ Server busy or request blocked."

    parsed = parse_india_post_html(post.text)
    if not parsed:
        return "❌ No tracking results found. Check AWB number."

    msg = [f"📦 Tracking: {awb}\n"]

    if "status" in parsed:
        dt_str = parsed.get("date_time", "")
        nice_dt = dt_str
        try:
            for fmt in ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %H:%M", "%d %b %Y %I:%M %p"):
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    nice_dt = dt.strftime("%d %b %Y — %I:%M %p")
                    break
                except:
                    pass
        except:
            pass

        msg.append(f"🟢 Status: {parsed['status']}")
        msg.append(f"📍 Location: {parsed.get('location','N/A')}")
        msg.append(f"🕒 Date & Time: {nice_dt}")
    else:
        msg.append(parsed.get("raw_row", ""))

    s = parsed.get("status", "").lower()
    if "return" in s or "returned" in s:
        msg.append("\n⚠️ Returned to Sender")
    if "redirect" in s or "redirected" in s:
        msg.append("\n🔄 Shipment Redirected")

    return "\n".join(msg)


def start(update, context):
    update.message.reply_text("Send me an India Post tracking number like EE123456789IN and I'll get the latest status.")


def handle_tracking(update, context):
    awb = update.message.text.strip().upper()
    update.message.reply_text("⏳ Tracking your parcel… please wait…")
    result = track_awb(awb)
    update.message.reply_text(result)


def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_tracking))

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
