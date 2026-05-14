import os
import json
import requests
from flask import Flask, render_template, request, jsonify
from datetime import datetime, date

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "")
BOOKINGS_FILE = "bookings.json"

WORK_START = 9
WORK_END = 18
SLOT_MINUTES = 30


def load_bookings():
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    with open(BOOKINGS_FILE, "r") as f:
        return json.load(f)


def save_bookings(bookings):
    with open(BOOKINGS_FILE, "w") as f:
        json.dump(bookings, f, ensure_ascii=False, indent=2)


def generate_slots():
    slots = []
    hour = WORK_START
    minute = 0
    while hour * 60 + minute < WORK_END * 60:
        slots.append(f"{hour:02d}:{minute:02d}")
        minute += SLOT_MINUTES
        if minute >= 60:
            minute -= 60
            hour += 1
    return slots


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/slots")
def slots():
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "date required"}), 400

    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "invalid date"}), 400

    if parsed.weekday() >= 5:
        return jsonify({"slots": []})

    bookings = load_bookings()
    booked = bookings.get(date_str, [])
    available = [s for s in generate_slots() if s not in booked]
    return jsonify({"slots": available})


@app.route("/book", methods=["POST"])
def book():
    data = request.json
    name = (data.get("name") or "").strip()
    contact = (data.get("contact") or "").strip()
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip()
    comment = (data.get("comment") or "").strip()

    if not all([name, contact, date_str, time_str]):
        return jsonify({"success": False, "error": "Заполните все поля"}), 400

    bookings = load_bookings()
    booked = bookings.get(date_str, [])

    if time_str in booked:
        return jsonify({"success": False, "error": "Этот слот уже занят"}), 409

    booked.append(time_str)
    bookings[date_str] = booked
    save_bookings(bookings)

    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        friendly_date = f"{days[parsed_date.weekday()]}, {parsed_date.day} {months[parsed_date.month - 1]}"
    except Exception:
        friendly_date = date_str

    message = (
        f"📅 <b>Новая запись!</b>\n\n"
        f"👤 <b>Имя:</b> {name}\n"
        f"📞 <b>Контакт:</b> {contact}\n"
        f"📆 <b>Дата:</b> {friendly_date}\n"
        f"🕐 <b>Время:</b> {time_str}\n"
    )
    if comment:
        message += f"💬 <b>Комментарий:</b> {comment}\n"

    if BOT_TOKEN and OWNER_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )

    return jsonify({"success": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
