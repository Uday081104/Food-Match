"""
whatsapp_sender.py — sends real WhatsApp messages via Meta's WhatsApp Cloud API.

This is NOT wired into app.py automatically because it needs your own
credentials (see README.md "Going live" section). Once you have them,
set the environment variables below and import send_whatsapp_message()
into app.py wherever a message needs to actually be sent (currently
those spots just log to message_log for the dashboard demo).
"""

import os
import requests

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")          # permanent/temporary access token
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")  # from Meta developer console

GRAPH_BASE = "https://graph.facebook.com/v20.0"
API_URL = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/messages" if PHONE_NUMBER_ID else None
MEDIA_URL = f"{GRAPH_BASE}/{PHONE_NUMBER_ID}/media" if PHONE_NUMBER_ID else None

UPLOAD_DIR = "uploads"


def _auth_headers():
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}


def send_whatsapp_image(to_phone: str, media_id: str, caption: str = ""):
    """Sends an image message using a media ID already uploaded to YOUR WhatsApp
    business number's media store (see upload_media below)."""
    headers = {**_auth_headers(), "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "image",
        "image": {"id": media_id, "caption": caption},
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def download_incoming_media(media_id: str) -> str:
    """
    Downloads a photo a user sent TO your WhatsApp number (identified by
    media_id from the webhook payload) and saves it locally.
    Returns the local file path.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Step 1: resolve the temporary media_id into an actual download URL
    meta_resp = requests.get(f"{GRAPH_BASE}/{media_id}", headers=_auth_headers(), timeout=15)
    meta_resp.raise_for_status()
    media_url = meta_resp.json()["url"]
    mime_type = meta_resp.json().get("mime_type", "image/jpeg")
    ext = "png" if "png" in mime_type else "jpg"

    # Step 2: download the actual bytes (also requires the auth header)
    file_resp = requests.get(media_url, headers=_auth_headers(), timeout=30)
    file_resp.raise_for_status()

    local_path = os.path.join(UPLOAD_DIR, f"{media_id}.{ext}")
    with open(local_path, "wb") as f:
        f.write(file_resp.content)
    return local_path


def upload_media(file_path: str) -> str:
    """
    Uploads a local file to YOUR WhatsApp business number's media store, so it
    can then be sent onward to a different recipient (e.g. forwarding a
    donor's photo to matched NGOs). Returns the new media_id.
    """
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, "image/jpeg")}
        data = {"messaging_product": "whatsapp"}
        response = requests.post(MEDIA_URL, headers=_auth_headers(), data=data, files=files, timeout=30)
    response.raise_for_status()
    return response.json()["id"]


def send_whatsapp_message(to_phone: str, body: str):
    """
    Sends a plain text WhatsApp message. `to_phone` must be in international
    format without '+' (e.g. '919812345678').
    """
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError(
            "WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID environment variables "
            "must be set before sending real messages. See README.md."
        )

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": body},
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()
