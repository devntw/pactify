import json
import os
import threading
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.services.audio import convert_to_wav, download_audio_from_twilio
from app.services.speech import transcribe_audio
from app.services.utils import (
    send_whatsapp_media_message,
    send_whatsapp_message,
    twilio_response,
)

router = APIRouter()

LAYER2_URL = os.getenv("LAYER2_URL", "http://layer2:8001/process")
LAYER2_CONFIRM_URL = os.getenv("LAYER2_CONFIRM_URL", "http://layer2:8001/confirm")
LAYER4_GENERATE_URL = os.getenv("LAYER4_GENERATE_URL", "http://layer4:8003/generate-proof")
LAYER4_FILE_URL = os.getenv("LAYER4_FILE_URL", "http://layer4:8003/proof-file")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or "").strip().rstrip("/")


def _build_reply(result: dict) -> str:
    missing = result.get("missing_fields") or []
    contract = result.get("contract") or {}
    ctype = contract.get("type")
    nq = (result.get("next_question") or "").strip()

    if nq and "timeline" in missing and ctype == "loan":
        return "❗ When should the money be returned?"
    if nq:
        return f"❗ {nq}"
    if result.get("is_complete"):
        return "✅ Contract recorded:\n" + json.dumps(contract, indent=2, default=str)
    return "❗ Please share a bit more detail about the agreement."


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    form = await request.form()

    phone = form.get("From") or ""
    text = form.get("Body") or ""
    media_url = form.get("MediaUrl0")
    account_sid = form.get("AccountSid") or ""
    message_sid = form.get("MessageSid") or ""

    print("[twilio] incoming:", phone, text, media_url)

    final_text = text
    audio_path = ""

    confirm_code = _extract_confirm_code(text)
    if confirm_code:
        try:
            response = requests.post(
                LAYER2_CONFIRM_URL,
                json={
                    "phone": str(phone),
                    "code": confirm_code,
                    "message_sid": str(message_sid),
                },
                timeout=30,
            )
            response.raise_for_status()
            confirm_result = response.json()
            public_base_url = _resolve_public_base_url(request)
            _send_final_agreement_artifacts(confirm_result, public_base_url)
            msg = (confirm_result.get("message") or "Confirmation processed.").strip()
            return twilio_response(f"✅ {msg}")
        except Exception as e:
            return twilio_response(f"Layer2 confirm error: {str(e)}")

    if media_url:
        threading.Thread(
            target=_process_audio_message_async,
            args=(
                str(phone),
                str(media_url),
                str(account_sid),
                str(message_sid),
            ),
            daemon=True,
        ).start()
        return twilio_response("✅ Voice note received. Processing now, you will get a reply shortly.")

    try:
        response = requests.post(
            LAYER2_URL,
            json={
                "phone": str(phone),
                "text": final_text or "",
                "audio_path": audio_path,
                "message_sid": str(message_sid),
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        return twilio_response(f"Layer2 error: {str(e)}")

    # Auto-notify counterparty so initiator does not need to relay the code manually.
    if result.get("confirmation_status") == "pending":
        code = (result.get("consent_code") or "").strip()
        counterparty = (result.get("counterparty_phone") or "").strip()
        if code and counterparty and counterparty != str(phone):
            outbound_text = (
                "VSCE contract confirmation request. "
                f"Please reply with: CONFIRM {code}"
            )
            outbound_result = send_whatsapp_message(counterparty, outbound_text)
            print("[twilio] outbound confirmation:", outbound_result)

    reply = _build_reply(result)
    return twilio_response(reply)


def _extract_confirm_code(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    parts = t.split()
    if len(parts) >= 2 and parts[0].upper() == "CONFIRM":
        return parts[1].strip().upper()
    return ""


def _send_final_agreement_artifacts(confirm_result: dict, public_base_url: str = "") -> None:
    if (confirm_result.get("status") or "").strip().lower() != "confirmed":
        return

    initiator = (confirm_result.get("initiator_phone") or "").strip()
    confirmer = (confirm_result.get("confirmer_phone") or "").strip()
    contract = confirm_result.get("contract") or {}
    if not isinstance(contract, dict):
        contract = {}

    proof_payload = {"layer2": confirm_result}
    try:
        proof_resp = requests.post(LAYER4_GENERATE_URL, json=proof_payload, timeout=30)
        proof_resp.raise_for_status()
        proof = proof_resp.json()
    except Exception as exc:
        print("[proof] generate failed:", exc)
        return

    contract_id = (proof.get("contract_id") or "").strip()
    h = (proof.get("hash") or "").strip()
    if not contract_id:
        return

    text = f"Agreement finalized. Contract ID: {contract_id}. Hash: {h}"
    base = (public_base_url or "").strip().rstrip("/")
    media_url = f"{base}/webhook/proof/{contract_id}.pdf" if base else ""

    recipients = [p for p in [initiator, confirmer] if p]
    for phone in recipients:
        if media_url:
            result = send_whatsapp_media_message(phone, text, media_url)
            print("[twilio] outbound final-media:", result)
            if result.get("status") != "sent":
                fallback = send_whatsapp_message(phone, text + f"\nPDF: {media_url}")
                print("[twilio] outbound final-fallback:", fallback)
        else:
            result = send_whatsapp_message(phone, text)
            print("[twilio] outbound final-text:", result)


def _process_audio_message_async(
    phone: str,
    media_url: str,
    account_sid: str,
    message_sid: str,
) -> None:
    try:
        audio_path = download_audio_from_twilio(media_url, account_sid=account_sid)
        audio_path = convert_to_wav(audio_path)
        final_text = transcribe_audio(audio_path)
        if not (final_text or "").strip():
            msg = (
                "I could not clearly understand the voice note. "
                "Please resend a clearer audio note or type the agreement text."
            )
            result = send_whatsapp_message(phone, msg)
            print("[twilio] outbound audio-empty:", result)
            return
    except Exception as exc:
        err = send_whatsapp_message(phone, f"Audio error: {exc}")
        print("[twilio] outbound audio-error:", err)
        return

    try:
        response = requests.post(
            LAYER2_URL,
            json={
                "phone": phone,
                "text": final_text or "",
                "audio_path": audio_path,
                "message_sid": message_sid,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        err = send_whatsapp_message(phone, f"Layer2 error: {exc}")
        print("[twilio] outbound layer2-error:", err)
        return

    if result.get("confirmation_status") == "pending":
        code = (result.get("consent_code") or "").strip()
        counterparty = (result.get("counterparty_phone") or "").strip()
        if code and counterparty and counterparty != phone:
            outbound_text = (
                "VSCE contract confirmation request. "
                f"Please reply with: CONFIRM {code}"
            )
            outbound_result = send_whatsapp_message(counterparty, outbound_text)
            print("[twilio] outbound confirmation:", outbound_result)

    reply = _build_reply(result)
    sent = send_whatsapp_message(phone, reply)
    print("[twilio] outbound audio-reply:", sent)


def _resolve_public_base_url(request: Request) -> str:
    configured = (PUBLIC_BASE_URL or "").strip().rstrip("/")
    if _looks_public_url(configured):
        return configured

    # Twilio webhook requests usually carry the public host/proto. Use this as fallback
    # so PDF media can work without explicit PUBLIC_BASE_URL env.
    proto = (
        request.headers.get("x-forwarded-proto")
        or request.url.scheme
        or "https"
    ).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
        or ""
    ).split(",")[0].strip()
    candidate = f"{proto}://{host}".rstrip("/") if host else ""

    if _looks_public_url(candidate):
        return candidate

    print("[proof] PUBLIC_BASE_URL missing or non-public; sending text fallback")
    return ""


def _looks_public_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    blocked_hosts = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "layer1",
        "layer2",
        "layer3",
        "layer4",
        "layer5",
    }
    if host in blocked_hosts:
        return False
    if host.endswith(".local"):
        return False

    return True


@router.get("/proof/{contract_id}.pdf")
def proof_proxy(contract_id: str):
    if not contract_id.strip():
        raise HTTPException(status_code=400, detail="contract_id required")
    url = f"{LAYER4_FILE_URL.rstrip('/')}/{contract_id}.pdf"
    try:
        resp = requests.get(url, timeout=30)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Layer4 fetch failed: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="proof file not found")

    return Response(content=resp.content, media_type="application/pdf")
