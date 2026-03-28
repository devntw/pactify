import json
import re

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    ConfirmRequest,
    ConfirmResponse,
    ContractPayload,
    ProcessRequest,
    ProcessResponse,
)
from app.services.contract import (
    compute_missing_fields,
    has_contract_signal,
    is_contract_complete,
    merge_contract,
    next_question_for,
    is_smalltalk_or_greeting,
)
from app.services.llm import extract_contract_with_llm
from app.services.forward import forward_to_layer3
from app.services.session import (
    clear_consent_for_initiator,
    confirm_by_code,
    get_consent_for_initiator,
    get_session,
    set_session_contract,
    upsert_pending_consent,
)

router = APIRouter(tags=["process"])


@router.post("/process", response_model=ProcessResponse)
def process_payload(body: ProcessRequest) -> ProcessResponse:
    if not body.phone or not body.phone.strip():
        raise HTTPException(status_code=400, detail="phone is required")

    phone = body.phone.strip()
    prior = get_session(phone)
    text = (body.text or "").strip()

    if is_smalltalk_or_greeting(text) and not has_contract_signal(prior):
        starter = (
            "Please describe the agreement in one message: who are the parties, "
            "what was agreed, amount (if any), and timeline."
        )
        missing = compute_missing_fields(prior)
        response = ProcessResponse(
            contract=ContractPayload.model_validate(prior),
            missing_fields=missing,
            is_complete=False,
            next_question=starter,
            confirmation_required=False,
            confirmation_status="not_started",
        )
        forward_to_layer3(
            {
                "phone": phone,
                "text": body.text,
                "audio_path": body.audio_path,
                "result": response.model_dump(),
            }
        )
        return response

    fresh = extract_contract_with_llm(body.text, body.audio_path)
    merged = merge_contract(prior, fresh)
    set_session_contract(phone, merged)

    missing = compute_missing_fields(merged)
    complete = is_contract_complete(merged)
    next_q = next_question_for(missing)

    response = ProcessResponse(
        contract=ContractPayload.model_validate(merged),
        missing_fields=missing,
        is_complete=complete,
        next_question=next_q,
    )

    consent = get_consent_for_initiator(phone)
    if consent and not _contracts_equivalent(consent.get("contract") or {}, merged):
        clear_consent_for_initiator(phone)
        consent = {}

    if consent:
        response.confirmation_required = True
        response.confirmation_status = consent.get("status", "pending")
        response.consent_code = consent.get("consent_code")
        response.counterparty_phone = consent.get("counterparty_phone")

    if complete:
        counterparty = _extract_whatsapp_phone(text)
        if not consent and not counterparty:
            response.is_complete = False
            response.confirmation_required = True
            response.confirmation_status = "not_started"
            response.next_question = (
                "To finalize, share the counterparty WhatsApp number in international format, "
                "for example +9198XXXXXX."
            )
        elif counterparty:
            if counterparty == phone:
                response.is_complete = False
                response.confirmation_required = True
                response.confirmation_status = "not_started"
                response.next_question = "Counterparty number must be different from your number."
            else:
                consent = upsert_pending_consent(
                    initiator_phone=phone,
                    counterparty_phone=counterparty,
                    contract=ContractPayload.model_validate(merged).model_dump(),
                    initiator_message_sid=body.message_sid,
                )
                response.is_complete = False
                response.confirmation_required = True
                response.confirmation_status = "pending"
                response.consent_code = consent.get("consent_code")
                response.counterparty_phone = counterparty
                response.next_question = (
                    f"Ask {counterparty} to send: CONFIRM {consent.get('consent_code')}"
                )
        elif consent.get("status") == "pending":
            response.is_complete = False
            response.confirmation_required = True
            response.next_question = (
                f"Pending confirmation from {consent.get('counterparty_phone')}. "
                f"Ask them to send: CONFIRM {consent.get('consent_code')}"
            )
        elif consent.get("status") == "confirmed":
            response.is_complete = True
            response.confirmation_required = True
            response.confirmation_status = "confirmed"
            response.next_question = ""

    forward_to_layer3(
        {
            "phone": phone,
            "text": body.text,
            "audio_path": body.audio_path,
            "result": response.model_dump(),
        }
    )
    return response


@router.post("/confirm", response_model=ConfirmResponse)
def confirm_payload(body: ConfirmRequest) -> ConfirmResponse:
    phone = (body.phone or "").strip()
    code = (body.code or "").strip().upper()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    if not code:
        raise HTTPException(status_code=400, detail="code is required")

    outcome = confirm_by_code(code, phone, body.message_sid)
    status = outcome.get("status", "not_found")

    if status == "not_found":
        response = ConfirmResponse(
            status="not_found",
            message="Confirmation code not found.",
            contract=ContractPayload(),
            initiator_phone="",
            confirmer_phone=phone,
        )
    elif status == "already_confirmed":
        record = outcome.get("record") or {}
        response = ConfirmResponse(
            status="already_confirmed",
            message="This agreement is already confirmed.",
            contract=ContractPayload.model_validate(record.get("contract") or {}),
            initiator_phone=record.get("initiator_phone", ""),
            confirmer_phone=phone,
        )
    elif status == "rejected":
        reason = outcome.get("reason")
        if reason == "initiator_cannot_self_confirm":
            msg = "Initiator cannot self-confirm. Counterparty must confirm from their number."
        elif reason == "wrong_counterparty":
            msg = "This code belongs to a different counterparty number."
        else:
            msg = "Confirmation rejected."
        response = ConfirmResponse(
            status="rejected",
            message=msg,
            contract=ContractPayload(),
            initiator_phone="",
            confirmer_phone=phone,
        )
    else:
        record = outcome.get("record") or {}
        response = ConfirmResponse(
            status="confirmed",
            message="Agreement confirmed by counterparty.",
            contract=ContractPayload.model_validate(record.get("contract") or {}),
            initiator_phone=record.get("initiator_phone", ""),
            confirmer_phone=phone,
        )

    forward_to_layer3(
        {
            "phone": phone,
            "text": f"CONFIRM {code}",
            "audio_path": "",
            "result": response.model_dump(),
        }
    )
    return response


def _extract_whatsapp_phone(text: str) -> str:
    t = (text or "")
    m = re.search(r"(\+\d{8,15})", t)
    if m:
        return "whatsapp:" + m.group(1)
    m = re.search(r"\b(\d{10,15})\b", t)
    if m:
        return "whatsapp:+" + m.group(1)
    return ""


def _contracts_equivalent(left: dict, right: dict) -> bool:
    try:
        return json.dumps(left or {}, sort_keys=True) == json.dumps(right or {}, sort_keys=True)
    except Exception:
        return False
