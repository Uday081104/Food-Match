"""
app.py — Surplus Donation Matcher backend (food, clothes, books, or anything else).

Runs a Flask server exposing:
  1. A JSON API used by the built-in browser dashboard (for testing/admin use)
  2. A WhatsApp Cloud API compatible webhook (/webhook) that lets donors and
     NGOs self-register and post/claim donations entirely through chat —
     no admin action needed once this is deployed.

To run locally:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000 in your browser.

See README.md for how to connect this to a real WhatsApp Business number.
"""

import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory

from db import init_db, get_db, log_message
from matching import find_matching_ngos, claim_offer
from whatsapp_sender import (
    send_whatsapp_message,
    send_whatsapp_image,
    download_incoming_media,
    upload_media,
    WHATSAPP_TOKEN,
    PHONE_NUMBER_ID,
)

app = Flask(__name__)

WHATSAPP_LIVE = bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID)
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "foodmatch_verify_token")

CATEGORIES = {"1": "Food", "2": "Clothes", "3": "Books", "4": "Electronics", "5": "Other"}
CATEGORY_MENU = "\n".join(f"{k}. {v}" for k, v in CATEGORIES.items())

# In-memory conversation state per phone number. Lost on server restart —
# fine for an MVP pilot; move to a DB table if you need durability.
# Shape: {"+91...": {"step": "awaiting_category", "data": {...}}}
CONVERSATIONS = {}


def deliver_message(to_phone, body):
    """Sends a WhatsApp text message if credentials are set, else just logs it."""
    log_message("outbound", to_phone, body)
    if WHATSAPP_LIVE:
        clean_phone = to_phone.replace("+", "").replace(" ", "")
        try:
            send_whatsapp_message(clean_phone, body)
        except Exception as e:
            print(f"[WhatsApp send failed for {to_phone}]: {e}")


def deliver_image(to_phone, media_id, caption):
    log_message("outbound", to_phone, f"[photo] {caption}")
    if WHATSAPP_LIVE:
        clean_phone = to_phone.replace("+", "").replace(" ", "")
        try:
            send_whatsapp_image(clean_phone, media_id, caption)
        except Exception as e:
            print(f"[WhatsApp image send failed for {to_phone}]: {e}")


# =========================================================
# Browser dashboard (admin/testing view — read-only for donations,
# since donors/NGOs now self-register and post via WhatsApp)
# =========================================================

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)


@app.route("/api/donors", methods=["GET", "POST"])
def api_donors():
    if request.method == "POST":
        d = request.json
        with get_db() as conn:
            conn.execute(
                "INSERT INTO donors (name, phone, pincode, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
                (d["name"], d["phone"], d["pincode"], d.get("latitude"), d.get("longitude")),
            )
        return jsonify({"status": "ok"})
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM donors ORDER BY id DESC").fetchall()
        return jsonify([dict(r) for r in rows])


@app.route("/api/ngos", methods=["GET", "POST"])
def api_ngos():
    if request.method == "POST":
        d = request.json
        with get_db() as conn:
            conn.execute(
                "INSERT INTO ngos (name, phone, pincode, latitude, longitude, capacity_notes) VALUES (?, ?, ?, ?, ?, ?)",
                (d["name"], d["phone"], d["pincode"], d.get("latitude"), d.get("longitude"), d.get("capacity_notes", "")),
            )
        return jsonify({"status": "ok"})
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM ngos ORDER BY id DESC").fetchall()
        return jsonify([dict(r) for r in rows])


@app.route("/api/ngos/<int:ngo_id>/availability", methods=["POST"])
def toggle_ngo_availability(ngo_id):
    available = 1 if request.json.get("is_available") else 0
    with get_db() as conn:
        conn.execute("UPDATE ngos SET is_available = ? WHERE id = ?", (available, ngo_id))
    return jsonify({"status": "ok"})


@app.route("/api/offers", methods=["GET", "POST"])
def api_offers():
    if request.method == "POST":
        # Kept for dashboard/admin testing without WhatsApp — mirrors create_offer() below.
        d = request.json
        offer_id, broadcast_log = create_offer(
            donor_id=d["donor_id"],
            category=d["category"],
            description=d["description"],
            pickup_location=d["pickup_location"],
            photo_path=None,
            photo_media_id=None,
            hours_until_pickup=int(d.get("hours_until_pickup", 4)),
        )
        return jsonify({"status": "ok", "offer_id": offer_id, "broadcast_to": broadcast_log})

    with get_db() as conn:
        rows = conn.execute(
            """SELECT offers.*, donors.name as donor_name
               FROM offers JOIN donors ON offers.donor_id = donors.id
               ORDER BY offers.id DESC"""
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.route("/api/offers/<int:offer_id>/claim", methods=["POST"])
def api_claim_offer(offer_id):
    ngo_id = request.json["ngo_id"]
    success, message = claim_offer(offer_id, ngo_id)
    return jsonify({"success": success, "message": message})


@app.route("/api/messages", methods=["GET"])
def api_messages():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM message_log ORDER BY id DESC LIMIT 100").fetchall()
        return jsonify([dict(r) for r in rows])


# =========================================================
# Shared offer-creation + broadcast logic (used by both the
# dashboard API above and the WhatsApp flow below)
# =========================================================

def create_offer(donor_id, category, description, pickup_location, photo_path, photo_media_id, hours_until_pickup=4):
    prepared_at = datetime.utcnow().isoformat()
    pickup_by = (datetime.utcnow() + timedelta(hours=hours_until_pickup)).isoformat()

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO offers
               (donor_id, category, description, photo_path, photo_media_id, prepared_at, pickup_by, pickup_location)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (donor_id, category, description, photo_path, photo_media_id, prepared_at, pickup_by, pickup_location),
        )
        offer_id = cur.lastrowid
        donor = conn.execute("SELECT * FROM donors WHERE id = ?", (donor_id,)).fetchone()

    matches = find_matching_ngos(donor["pincode"], donor["latitude"], donor["longitude"])

    broadcast_log = []
    caption = (
        f"📦 New donation available!\n"
        f"From: {donor['name']}\n"
        f"Category: {category}\n"
        f"Details: {description}\n"
        f"Pickup by: {pickup_by[:16].replace('T', ' ')}\n"
        f"Location: {pickup_location}\n\n"
        f"Reply CONFIRM {offer_id} to claim this (first come, first served)."
    )
    for ngo in matches:
        if photo_media_id and WHATSAPP_LIVE:
            deliver_image(ngo["phone"], photo_media_id, caption)
        else:
            deliver_message(ngo["phone"], caption)
        broadcast_log.append({"ngo": ngo["name"], "phone": ngo["phone"], "message": caption})

    return offer_id, broadcast_log


# =========================================================
# WhatsApp Cloud API webhook — self-service onboarding + posting
# =========================================================

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    payload = request.get_json(silent=True) or {}

    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        message = entry["messages"][0]
        from_phone = message["from"]
        msg_type = message.get("type", "text")
        text = message.get("text", {}).get("body", "").strip() if msg_type == "text" else ""
        image = message.get("image") if msg_type == "image" else None
    except (KeyError, IndexError):
        return jsonify({"status": "ignored"}), 200

    log_message("inbound", from_phone, text if text else "[photo]")
    reply = handle_incoming_message(from_phone, text, image)
    if reply:
        deliver_message(from_phone, reply)
    return jsonify({"status": "ok", "reply": reply}), 200


def get_donor(phone):
    with get_db() as conn:
        return conn.execute("SELECT * FROM donors WHERE phone = ?", (phone,)).fetchone()


def get_ngo(phone):
    with get_db() as conn:
        return conn.execute("SELECT * FROM ngos WHERE phone = ?", (phone,)).fetchone()


def handle_incoming_message(phone, text, image):
    """
    Full self-service state machine. Anyone can message the bot cold —
    no admin needs to register them first.

    Global commands (work anytime, registered or not):
      MENU / HELP   -> show help
      RESET         -> cancel whatever flow they're mid-way through

    Unregistered users: role selection -> name -> pincode -> registered.
    Registered donors:  DONATE (or just sending a photo) starts a donation post.
    Registered NGOs:    AVAILABLE / UNAVAILABLE / CONFIRM <id>
    """
    state = CONVERSATIONS.get(phone, {"step": None, "data": {}})
    text_upper = text.upper().strip()

    if text_upper in ("MENU", "HELP"):
        CONVERSATIONS.pop(phone, None)
        return help_text(phone)

    if text_upper == "RESET":
        CONVERSATIONS.pop(phone, None)
        return "Okay, cancelled. Send MENU to see your options."

    donor = get_donor(phone)
    ngo = get_ngo(phone)

    # ---------- Mid-flow: continue wherever they left off ----------
    if state["step"]:
        return continue_flow(phone, state, text, text_upper, image)

    # ---------- Not registered anywhere yet: start onboarding ----------
    if not donor and not ngo:
        if text_upper in ("1", "DONATE", "DONOR"):
            CONVERSATIONS[phone] = {"step": "reg_donor_name", "data": {}}
            return "Great! What's your name (or your shop/household name)?"
        if text_upper in ("2", "NGO", "SHELTER"):
            CONVERSATIONS[phone] = {"step": "reg_ngo_name", "data": {}}
            return "Great! What's your NGO/shelter's name?"
        return (
            "👋 Welcome to the Community Donation Matcher!\n"
            "Are you looking to:\n"
            "1. Donate something (food, clothes, books, anything)\n"
            "2. Register as an NGO/shelter to receive donations\n\n"
            "Reply 1 or 2."
        )

    # ---------- Registered donor ----------
    if donor:
        if text_upper == "DONATE":
            CONVERSATIONS[phone] = {"step": "donate_category", "data": {}}
            return f"What are you donating? Reply with a number:\n{CATEGORY_MENU}"
        if image:
            # Donor sent a photo directly, no DONATE command first — start the flow from here.
            CONVERSATIONS[phone] = {"step": "donate_category", "data": {"pending_image": image}}
            return f"Got the photo! What category is this? Reply with a number:\n{CATEGORY_MENU}"
        return help_text(phone)

    # ---------- Registered NGO ----------
    if ngo:
        if text_upper.startswith("CONFIRM"):
            parts = text_upper.split()
            if len(parts) == 2 and parts[1].isdigit():
                success, msg = claim_offer(int(parts[1]), ngo["id"])
                return f"✅ {msg}" if success else f"❌ {msg}"
            return "To claim a donation, reply like: CONFIRM 12"
        if text_upper == "AVAILABLE":
            with get_db() as conn:
                conn.execute("UPDATE ngos SET is_available = 1 WHERE phone = ?", (phone,))
            return "You're marked as available to receive donation alerts. Reply UNAVAILABLE to pause."
        if text_upper == "UNAVAILABLE":
            with get_db() as conn:
                conn.execute("UPDATE ngos SET is_available = 0 WHERE phone = ?", (phone,))
            return "You're marked as unavailable. Reply AVAILABLE anytime to resume."
        return help_text(phone)

    return help_text(phone)


def continue_flow(phone, state, text, text_upper, image):
    step = state["step"]
    data = state["data"]

    # ---- Donor registration ----
    if step == "reg_donor_name":
        data["name"] = text.strip()
        state["step"] = "reg_donor_pincode"
        CONVERSATIONS[phone] = state
        return "Thanks! What's your pincode/area code?"

    if step == "reg_donor_pincode":
        pincode = text.strip()
        if not pincode.isdigit() or len(pincode) < 4:
            return "That doesn't look like a valid pincode. Please send just the digits, e.g. 400001."
        with get_db() as conn:
            conn.execute(
                "INSERT INTO donors (name, phone, pincode) VALUES (?, ?, ?)",
                (data["name"], phone, pincode),
            )
        CONVERSATIONS.pop(phone, None)
        return (
            f"🎉 You're registered as a donor, {data['name']}!\n"
            "Whenever you have something to give away — food, clothes, books, "
            "anything — just reply DONATE, or send a photo of the item straight away."
        )

    # ---- NGO registration ----
    if step == "reg_ngo_name":
        data["name"] = text.strip()
        state["step"] = "reg_ngo_pincode"
        CONVERSATIONS[phone] = state
        return "Thanks! What's your pincode/area code?"

    if step == "reg_ngo_pincode":
        pincode = text.strip()
        if not pincode.isdigit() or len(pincode) < 4:
            return "That doesn't look like a valid pincode. Please send just the digits, e.g. 400001."
        with get_db() as conn:
            conn.execute(
                "INSERT INTO ngos (name, phone, pincode, is_available) VALUES (?, ?, ?, 1)",
                (data["name"], phone, pincode),
            )
        CONVERSATIONS.pop(phone, None)
        return (
            f"🎉 You're registered, {data['name']}! You'll now get alerts for donations near you.\n"
            "Reply UNAVAILABLE anytime you can't accept donations, and AVAILABLE to resume.\n"
            "When you see one you want, reply CONFIRM <id>."
        )

    # ---- Donation posting flow ----
    if step == "donate_category":
        category = CATEGORIES.get(text.strip())
        if not category:
            return f"Please reply with just a number:\n{CATEGORY_MENU}"
        data["category"] = category
        state["step"] = "donate_description"
        CONVERSATIONS[phone] = state
        return "Got it. Briefly describe what you're donating (e.g. '3 bags of kids' clothes' or '20 veg meals')."

    if step == "donate_description":
        data["description"] = text.strip()
        if data.get("pending_image"):
            # Photo already attached before category was even picked — skip ahead.
            state["step"] = "donate_location"
            CONVERSATIONS[phone] = state
            return "Thanks! Where should NGOs pick this up from? (Send an address or landmark)"
        state["step"] = "donate_photo"
        CONVERSATIONS[phone] = state
        return "Want to attach a photo? Send it now, or reply SKIP."

    if step == "donate_photo":
        if image:
            data["pending_image"] = image
        elif text_upper != "SKIP":
            return "Please send a photo, or reply SKIP to continue without one."
        state["step"] = "donate_location"
        CONVERSATIONS[phone] = state
        return "Where should NGOs pick this up from? (Send an address or landmark)"

    if step == "donate_location":
        donor = get_donor(phone)
        photo_path, photo_media_id = None, None

        img = data.get("pending_image")
        if img:
            media_id = img.get("id")
            try:
                photo_path = download_incoming_media(media_id) if WHATSAPP_TOKEN else None
                photo_media_id = upload_media(photo_path) if photo_path else None
            except Exception as e:
                print(f"[media handling failed]: {e}")

        offer_id, broadcast_log = create_offer(
            donor_id=donor["id"],
            category=data["category"],
            description=data["description"],
            pickup_location=text.strip(),
            photo_path=photo_path,
            photo_media_id=photo_media_id,
        )
        CONVERSATIONS.pop(phone, None)
        return (
            f"✅ Posted! Donation #{offer_id} sent to {len(broadcast_log)} nearby NGO(s).\n"
            "You'll be notified here once one of them confirms pickup."
        )

    # Fallback: shouldn't normally reach here
    CONVERSATIONS.pop(phone, None)
    return help_text(phone)


def help_text(phone):
    donor = get_donor(phone)
    ngo = get_ngo(phone)
    if donor:
        return "Reply DONATE to post a new donation, or just send a photo of the item directly."
    if ngo:
        return "Reply AVAILABLE / UNAVAILABLE to toggle your status, or CONFIRM <id> to claim a donation."
    return (
        "👋 Welcome to the Community Donation Matcher!\n"
        "Are you looking to:\n"
        "1. Donate something (food, clothes, books, anything)\n"
        "2. Register as an NGO/shelter to receive donations\n\n"
        "Reply 1 or 2."
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
