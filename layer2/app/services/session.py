from threading import Lock
from datetime import datetime, timezone
import random
import string
from typing import Any, Dict

from app.models.schemas import empty_contract_dict

_lock = Lock()
_sessions: Dict[str, Dict[str, Any]] = {}
_consents_by_initiator: Dict[str, Dict[str, Any]] = {}
_consents_by_code: Dict[str, str] = {}


def get_session(phone: str) -> Dict[str, Any]:
    with _lock:
        if phone not in _sessions:
            _sessions[phone] = empty_contract_dict()
        return _sessions[phone]


def set_session_contract(phone: str, contract: Dict[str, Any]) -> None:
    with _lock:
        _sessions[phone] = contract


def clear_session(phone: str) -> None:
    with _lock:
        _sessions.pop(phone, None)


def get_consent_for_initiator(initiator_phone: str) -> Dict[str, Any]:
    with _lock:
        return dict(_consents_by_initiator.get(initiator_phone, {}))


def clear_consent_for_initiator(initiator_phone: str) -> None:
    with _lock:
        record = _consents_by_initiator.pop(initiator_phone, None)
        if record:
            _consents_by_code.pop(record.get("consent_code", ""), None)


def upsert_pending_consent(
    initiator_phone: str,
    counterparty_phone: str,
    contract: Dict[str, Any],
    initiator_message_sid: str = "",
) -> Dict[str, Any]:
    with _lock:
        prior = _consents_by_initiator.get(initiator_phone)
        if prior and prior.get("status") == "pending":
            _consents_by_code.pop(prior.get("consent_code", ""), None)

        code = _generate_code()
        while code in _consents_by_code:
            code = _generate_code()

        record = {
            "initiator_phone": initiator_phone,
            "counterparty_phone": counterparty_phone,
            "contract": contract,
            "consent_code": code,
            "status": "pending",
            "created_at": _now_iso(),
            "confirmed_at": None,
            "initiator_message_sid": initiator_message_sid,
            "counterparty_message_sid": "",
        }
        _consents_by_initiator[initiator_phone] = record
        _consents_by_code[code] = initiator_phone
        return dict(record)


def confirm_by_code(
    code: str,
    confirmer_phone: str,
    confirmer_message_sid: str = "",
) -> Dict[str, Any]:
    with _lock:
        initiator = _consents_by_code.get(code)
        if not initiator:
            return {"status": "not_found"}

        record = _consents_by_initiator.get(initiator)
        if not record:
            return {"status": "not_found"}

        if record.get("status") == "confirmed":
            return {"status": "already_confirmed", "record": dict(record)}

        if confirmer_phone == initiator:
            return {"status": "rejected", "reason": "initiator_cannot_self_confirm"}

        expected = (record.get("counterparty_phone") or "").strip()
        if expected and confirmer_phone != expected:
            return {"status": "rejected", "reason": "wrong_counterparty"}

        record["status"] = "confirmed"
        record["confirmed_at"] = _now_iso()
        record["counterparty_message_sid"] = confirmer_message_sid
        _consents_by_initiator[initiator] = record
        return {"status": "confirmed", "record": dict(record)}


def _generate_code() -> str:
    # 6-char alphanumeric code for easy WhatsApp typing.
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(6))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
