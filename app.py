import os
import uuid
import requests
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta, date
import pytz

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "")
YANDEX_LOGIN = os.environ.get("YANDEX_LOGIN", "")
YANDEX_PASSWORD = os.environ.get("YANDEX_PASSWORD", "")
CALDAV_URL = f"https://caldav.yandex.ru/calendars/{YANDEX_LOGIN}/"

TZ = pytz.timezone("Europe/Moscow")
WORK_START = 9
WORK_END = 18
SLOT_MINUTES = 30


def generate_slots():
    slots = []
    hour, minute = WORK_START, 0
    while hour * 60 + minute < WORK_END * 60:
        slots.append(f"{hour:02d}:{minute:02d}")
        minute += SLOT_MINUTES
        if minute >= 60:
            minute -= 60
            hour += 1
    return slots


def get_caldav_client():
    import caldav
    return caldav.DAVClient(
        url=CALDAV_URL,
        username=YANDEX_LOGIN,
        password=YANDEX_PASSWORD,
    )


def get_calendar():
    client = get_caldav_client()
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("Нет доступных календарей")
    return calendars[0]


def to_aware(dt, fallback_date=None):
    """Convert date or naive datetime to Moscow-aware datetime."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return TZ.localize(dt)
        return dt.astimezone(TZ)
    # all-day event (date object)
    return TZ.localize(datetime.combine(dt, datetime.min.time()))


def get_busy_slots(date_str):
    try:
        from icalendar import Calendar as iCal

        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        day_start = TZ.localize(parsed_date.replace(hour=0, minute=0, second=0))
        day_end = TZ.localize(parsed_date.replace(hour=23, minute=59, second=59))

        calendar = get_calendar()
        events = calendar.date_search(start=day_start, end=day_end, expand=True)

        busy = set()
        all_slots = generate_slots()

        for event in events:
            cal = iCal.from_ical(event.data)
            for comp in cal.walk():
                if comp.name != "VEVENT":
                    continue
                dtstart = to_aware(comp.get("DTSTART").dt)
                dtend = to_aware(comp.get("DTEND").dt)

                for slot in all_slots:
                    h, m = map(int, slot.split(":"))
                    slot_start = TZ.localize(parsed_date.replace(hour=h, minute=m, second=0))
                    slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
                    if slot_start < dtend and slot_end > dtstart:
                        busy.add(slot)

        return list(busy)
    except Exception as e:
        print(f"[CalDAV read error] {e}")
        return []


def create_event(date_str, time_str, name, contact, comment):
    try:
        from icalendar import Calendar as iCal, Event

        parsed = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dtstart = TZ.localize(parsed)
        dtend = dtstart + timedelta(minutes=SLOT_MINUTES)

        cal = iCal()
        cal.add("prodid", "-//Booking Bot//RU")
        cal.add("version", "2.0")

        ev = Event()
        ev.add("uid", str(uuid.uuid4()))
        ev.add("summary", f"Встреча: {name}")
        description = f"Контакт: {contact}"
        if comment:
            description += f"\n{comment}"
        ev.add("description", description)
        ev.add("dtstart", dtstart)
        ev.add("dtend", dtend)
        ev.add("dtstamp", datetime.now(TZ))
        cal.add_component(ev)

        calendar = get_calendar()
        calendar.save_event(cal.to_ical().decode("utf-8"))
        return True
    except Exception as e:
        print(f"[CalDAV write error] {e}")
        return False


@app.route("/debug/caldav")
def debug_caldav():
    try:
        import caldav
        client = caldav.DAVClient(
            url=f"https://caldav.yandex.ru/calendars/{YANDEX_LOGIN}/",
            username=YANDEX_LOGIN,
            password=YANDEX_PASSWORD,
        )
        principal = client.principal()
        calendars = principal.calendars()
        names = [str(c.name) for c in calendars]
        return jsonify({"ok": True, "calendars": names, "login": YANDEX_LOGIN})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "login": YANDEX_LOGIN})


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

    busy = get_busy_slots(date_str)
    available = [s for s in generate_slots() if s not in busy]
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

    # Double-check slot is still free
    busy = get_busy_slots(date_str)
    if time_str in busy:
        return jsonify({"success": False, "error": "Этот слот уже занят, выберите другое время"}), 409

    # Create event in Yandex Calendar
    create_event(date_str, time_str, name, contact, comment)

    # Notify owner in Telegram
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
        months = ["января","февраля","марта","апреля","мая","июня",
                  "июля","августа","сентября","октября","ноября","декабря"]
        days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
        friendly = f"{days[parsed_date.weekday()]}, {parsed_date.day} {months[parsed_date.month - 1]}"
    except Exception:
        friendly = date_str

    message = (
        f"📅 <b>Новая запись!</b>\n\n"
        f"👤 <b>Имя:</b> {name}\n"
        f"📞 <b>Контакт:</b> {contact}\n"
        f"📆 <b>Дата:</b> {friendly}\n"
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
