import os
import json
import hmac
import hashlib
import logging
import shutil
from pathlib import Path
from decimal import Decimal, InvalidOperation

import requests
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

DATA_FILE = Path(os.getenv("DATA_FILE", "staff_data.json"))
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v27.0")

# Add your own WhatsApp number in international format, digits only.
# Example: 919876543210
ALLOWED_SENDERS = {
    item.strip().replace("+", "")
    for item in os.getenv("ALLOWED_SENDERS", "").split(",")
    if item.strip()
}

def load_records():
    # If DATA_FILE points to a newly mounted Railway Volume, seed it once
    # from the repository's bundled staff_data.json on first access.
    if not DATA_FILE.exists():
        bundled_file = Path("staff_data.json")
        if bundled_file.exists() and bundled_file.resolve() != DATA_FILE.resolve():
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_file, DATA_FILE)
    with DATA_FILE.open("r", encoding="utf-8") as file:
        records = json.load(file)
    if not isinstance(records, list):
        raise ValueError("staff_data.json must contain a JSON array")
    return records

def save_records(records):
    """Atomically replace the shared staff register."""
    if not isinstance(records, list):
        raise ValueError("Records must be a JSON array")
    required = {"Sl. No.", "Name", "Staff Number"}
    seen_serials = set()
    cleaned = []
    for row in records:
        if not isinstance(row, dict) or not required.issubset(row.keys()):
            raise ValueError("Every record must include Sl. No., Name and Staff Number")
        serial = str(row.get("Sl. No.", "")).strip()
        if not serial or serial in seen_serials:
            raise ValueError("Serial numbers must be present and unique")
        seen_serials.add(serial)
        cleaned.append({
            "Sl. No.": serial,
            "Name": str(row.get("Name", "")).strip(),
            "Staff Number": str(row.get("Staff Number", "")).strip(),
            "Member Number": str(row.get("Member Number", "") or "").strip(),
            "Date": str(row.get("Date", "") or "").strip(),
            "Status": str(row.get("Status", "Pending") or "Pending").strip(),
            "Receipt No.": str(row.get("Receipt No.", "") or "").strip(),
            "Amount": str(row.get("Amount", "") or "").strip(),
        })
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(cleaned, file, ensure_ascii=False, indent=2)
    temp_file.replace(DATA_FILE)
    return cleaned

@app.after_request
def add_cors_headers(response):
    # Public GitHub Pages frontend. This intentionally allows any website to call the API.
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response

@app.route("/api/records", methods=["GET", "PUT", "OPTIONS"])
def api_records():
    if request.method == "OPTIONS":
        return make_response("", 204)
    if request.method == "GET":
        try:
            records = load_records()
            return jsonify({"ok": True, "count": len(records), "records": records})
        except Exception:
            logging.exception("Could not read staff register through API")
            return jsonify({"ok": False, "error": "Could not read staff register"}), 500
    payload = request.get_json(silent=True)
    records = payload.get("records") if isinstance(payload, dict) else None
    try:
        saved = save_records(records)
        logging.warning("Staff register overwritten through public dashboard API; records=%d", len(saved))
        return jsonify({"ok": True, "count": len(saved), "message": "Staff register saved to Railway"})
    except (ValueError, TypeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception:
        logging.exception("Could not save staff register through API")
        return jsonify({"ok": False, "error": "Could not save staff register"}), 500

@app.get("/api/health")
def api_health():
    return jsonify({"ok": True, "service": "staff-whatsapp-bot", "api": "online"})

def normalize_staff_number(value):
    return "".join(ch for ch in str(value).strip() if ch.isdigit())

def find_staff(staff_number):
    wanted = normalize_staff_number(staff_number)
    if not wanted:
        return []
    records = load_records()
    return [
        row for row in records
        if normalize_staff_number(row.get("Staff Number", "")) == wanted
    ]

def display(value):
    if value is None or str(value).strip() in ("", "None"):
        return "Not recorded"
    return str(value).strip()

def format_amount(value):
    raw = display(value)
    if raw == "Not recorded":
        return raw
    try:
        return "₹{:,.2f}".format(Decimal(raw.replace(",", "").replace("₹", ""))).rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError):
        return raw

def format_record(row):
    return (
        "*Staff details*\n"
        f"Serial No: {display(row.get('Sl. No.'))}\n"
        f"Name: {display(row.get('Name'))}\n"
        f"Staff Number: {display(row.get('Staff Number'))}\n"
        f"Member Number: {display(row.get('Member Number'))}\n"
        f"Date: {display(row.get('Date'))}\n"
        f"Status: {display(row.get('Status'))}\n"
        f"Receipt No: {display(row.get('Receipt No.'))}\n"
        f"Amount: {format_amount(row.get('Amount'))}"
    )

def verify_meta_signature(raw_body):
    # Strongly recommended in production. Set META_APP_SECRET in Railway.
    if not META_APP_SECRET:
        logging.warning("META_APP_SECRET is not configured; webhook signature check is skipped")
        return True
    supplied = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        META_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)

def send_whatsapp_message(recipient, message):
    if not all([WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID]):
        raise RuntimeError("Missing WHATSAPP_ACCESS_TOKEN or PHONE_NUMBER_ID")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=25)
    if not response.ok:
        logging.error("WhatsApp API error %s: %s", response.status_code, response.text)
    response.raise_for_status()
    logging.info("WhatsApp reply accepted by Graph API")

def handle_incoming_message(message):
    sender = str(message.get("from", "")).replace("+", "")
    if not sender:
        return

    # This is a private staff lookup bot. Ignore unapproved senders.
    if ALLOWED_SENDERS and sender not in ALLOWED_SENDERS:
        logging.warning("Ignored message from non-allowlisted sender (sender suffix=%s)", sender[-4:])
        return

    if message.get("type") != "text":
        send_whatsapp_message(sender, "Please send the staff number as text.")
        return

    text = message.get("text", {}).get("body", "").strip()
    if text.lower() in {"hi", "hello", "help", "start"}:
        send_whatsapp_message(
            sender,
            "Welcome to Staff Register Lookup.\\n"
            "Send a staff number to view its record.\\n"
            "Example: 811895"
        )
        return

    staff_number = normalize_staff_number(text)
    if not staff_number:
        send_whatsapp_message(sender, "Please send a valid numeric staff number, e.g. 811895.")
        return

    matches = find_staff(staff_number)
    if not matches:
        send_whatsapp_message(sender, f"No record found for staff number {staff_number}. Please check the number.")
    elif len(matches) == 1:
        send_whatsapp_message(sender, format_record(matches[0]))
    else:
        # The supplied register has duplicate staff numbers. Never silently choose one.
        parts = [f"Found {len(matches)} records for staff number {staff_number}:"]
        for row in matches:
            parts.append(
                f"\\nSerial: {display(row.get('Sl. No.'))}"
                f"\\nName: {display(row.get('Name'))}"
                f"\\nMember No: {display(row.get('Member Number'))}"
                f"\\nDate: {display(row.get('Date'))}"
                f"\\nStatus: {display(row.get('Status'))}"
                f"\\nReceipt: {display(row.get('Receipt No.'))}"
                f"\\nAmount: {format_amount(row.get('Amount'))}\\n"
            )
        send_whatsapp_message(sender, "\\n".join(parts))

@app.get("/")
def health():
    return "Staff WhatsApp bot is running.", 200

@app.get("/webhook")
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")
    if mode == "subscribe" and token and hmac.compare_digest(token, VERIFY_TOKEN):
        return challenge, 200
    return "Webhook verification failed", 403

@app.post("/webhook")
def receive_webhook():
    logging.info("Meta webhook POST received")
    raw_body = request.get_data()
    if not verify_meta_signature(raw_body):
        return "Invalid signature", 403

    payload = request.get_json(silent=True) or {}
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                if messages:
                    logging.info("Webhook contains %d incoming message(s)", len(messages))
                else:
                    logging.info("Webhook received with no incoming messages (possibly a status update or test event)")
                for message in messages:
                    logging.info("Processing incoming WhatsApp message; type=%s", message.get("type", "unknown"))
                    handle_incoming_message(message)
    except Exception:
        # Return 200 so Meta does not endlessly retry malformed/failed events.
        logging.exception("Could not process WhatsApp webhook event")
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
