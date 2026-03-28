from xml.sax.saxutils import escape
import os
import time

import requests

from fastapi.responses import Response


def twilio_response(message: str) -> Response:
    """Return TwiML so Twilio can deliver a WhatsApp/SMS reply."""
    safe = escape(message or "")
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe}</Message>
</Response>
"""
    return Response(content=body, media_type="application/xml")


def send_whatsapp_message(to_phone: str, body: str) -> dict:
    """Send outbound WhatsApp message via Twilio API (best-effort)."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()

    if not sid or not token or not from_number:
        return {
            "status": "skipped",
            "reason": "twilio_outbound_not_configured",
            "to": to_phone,
        }

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    delays = [1.5, 3.0, 6.0]
    last_error = ""

    for attempt in range(len(delays) + 1):
        try:
            resp = requests.post(
                url,
                data={
                    "To": to_phone,
                    "From": from_number,
                    "Body": body,
                },
                auth=(sid, token),
                timeout=30,
            )

            if resp.status_code < 400:
                data = resp.json()
                return {"status": "sent", "sid": data.get("sid"), "to": to_phone}

            # Retry only on transient Twilio errors like 429 / 5xx.
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if attempt < len(delays):
                    wait_s = float(retry_after) if retry_after and retry_after.isdigit() else delays[attempt]
                    time.sleep(wait_s)
                    continue

            last_error = f"{resp.status_code} {resp.text[:300]}"
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue

    return {"status": "error", "to": to_phone, "error": last_error or "unknown_error"}


def send_whatsapp_media_message(to_phone: str, body: str, media_url: str) -> dict:
    """Send outbound WhatsApp message with media URL via Twilio API."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()

    if not sid or not token or not from_number:
        return {
            "status": "skipped",
            "reason": "twilio_outbound_not_configured",
            "to": to_phone,
        }

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    delays = [1.5, 3.0, 6.0]
    last_error = ""

    for attempt in range(len(delays) + 1):
        try:
            resp = requests.post(
                url,
                data={
                    "To": to_phone,
                    "From": from_number,
                    "Body": body,
                    "MediaUrl": media_url,
                },
                auth=(sid, token),
                timeout=30,
            )

            if resp.status_code < 400:
                data = resp.json()
                return {
                    "status": "sent",
                    "sid": data.get("sid"),
                    "to": to_phone,
                    "media_url": media_url,
                }

            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if attempt < len(delays):
                    wait_s = float(retry_after) if retry_after and retry_after.isdigit() else delays[attempt]
                    time.sleep(wait_s)
                    continue

            last_error = f"{resp.status_code} {resp.text[:300]}"
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue

    return {
        "status": "error",
        "to": to_phone,
        "media_url": media_url,
        "error": last_error or "unknown_error",
    }
