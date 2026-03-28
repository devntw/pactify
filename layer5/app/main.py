import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(title="VSCE Layer 5", version="1.0.0")

# Simple in-memory stores.
contracts: Dict[str, Dict[str, Any]] = {}
reputation: Dict[str, Dict[str, Any]] = {}

REMINDER_LEAD_SECONDS = int(os.getenv("L5_REMINDER_LEAD_SECONDS", "3600"))
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("L5_SCHEDULER_INTERVAL_SECONDS", "60"))
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()


class RegisterContractRequest(BaseModel):
    contract_id: str
    contract: Dict[str, Any]
    timestamp: Optional[str] = None


class DisputeRequest(BaseModel):
    contract_id: str
    reason: str = "counterparty dispute"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_or_now(raw: Optional[str]) -> datetime:
    if not raw:
        return _now()
    text = raw.strip()
    if not text:
        return _now()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return _now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_timeline_to_deadline(created_at: datetime, timeline: Any) -> Optional[datetime]:
    if timeline is None:
        return None
    t = str(timeline).strip().lower()
    if not t:
        return None

    if t == "today":
        return created_at
    if t == "tomorrow":
        return created_at + timedelta(days=1)

    m = re.search(r"(\d+)\s*(minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)", t)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("minute"):
            return created_at + timedelta(minutes=qty)
        if unit.startswith("hour"):
            return created_at + timedelta(hours=qty)
        if unit.startswith("day"):
            return created_at + timedelta(days=qty)
        if unit.startswith("week"):
            return created_at + timedelta(weeks=qty)
        if unit.startswith("month"):
            return created_at + timedelta(days=30 * qty)
        if unit.startswith("year"):
            return created_at + timedelta(days=365 * qty)

    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    if date_match:
        try:
            dt = datetime.fromisoformat(date_match.group(1))
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    return None


def _extract_timeline(contract: Dict[str, Any]) -> Optional[str]:
    terms = contract.get("terms") if isinstance(contract, dict) else None
    if isinstance(terms, dict):
        timeline = terms.get("timeline")
        if timeline is not None:
            return str(timeline)

    # Fallback for nested layer payloads.
    inner = contract.get("contract") if isinstance(contract, dict) else None
    if isinstance(inner, dict):
        terms = inner.get("terms")
        if isinstance(terms, dict) and terms.get("timeline") is not None:
            return str(terms.get("timeline"))

    # Layer4 proof shape: contract -> layer2 -> contract -> terms.timeline
    l2 = contract.get("layer2") if isinstance(contract, dict) else None
    if isinstance(l2, dict):
        l2_contract = l2.get("contract")
        if isinstance(l2_contract, dict):
            terms = l2_contract.get("terms")
            if isinstance(terms, dict) and terms.get("timeline") is not None:
                return str(terms.get("timeline"))

    # Extra fallback: recursive search for a terms.timeline value.
    nested_timeline = _find_timeline_recursive(contract)
    if nested_timeline is not None:
        return nested_timeline

    return None


def _extract_party_phones(contract: Dict[str, Any]) -> List[str]:
    phones: List[str] = []

    def add(raw: Any) -> None:
        if raw is None:
            return
        s = str(raw).strip()
        if not s:
            return
        if s.startswith("whatsapp:+"):
            phones.append(s)
            return
        if s.startswith("+") and s[1:].isdigit():
            phones.append("whatsapp:" + s)
            return

    # Common fields used in layer2 confirmation payload.
    add(contract.get("initiator_phone"))
    add(contract.get("confirmer_phone"))

    nested = contract.get("contract") if isinstance(contract, dict) else None
    if isinstance(nested, dict):
        add(nested.get("initiator_phone"))
        add(nested.get("confirmer_phone"))

    # Layer4 proof shape: contract -> layer2 -> {initiator_phone, confirmer_phone}
    l2 = contract.get("layer2") if isinstance(contract, dict) else None
    if isinstance(l2, dict):
        add(l2.get("initiator_phone"))
        add(l2.get("confirmer_phone"))
        l2_contract = l2.get("contract")
        if isinstance(l2_contract, dict):
            add(l2_contract.get("initiator_phone"))
            add(l2_contract.get("confirmer_phone"))

    # Generic deep scan for whatsapp:+ numbers.
    text = str(contract)
    for m in re.findall(r"whatsapp:\+\d{8,15}", text):
        phones.append(m)

    unique: List[str] = []
    seen = set()
    for p in phones:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _extract_violating_party(contract: Dict[str, Any]) -> str:
    obligations = contract.get("obligations")
    if not isinstance(obligations, list):
        inner = contract.get("contract") if isinstance(contract, dict) else None
        if isinstance(inner, dict):
            obligations = inner.get("obligations")
    if isinstance(obligations, list) and obligations:
        first = obligations[0]
        if isinstance(first, dict):
            party = first.get("party") or first.get("from_party")
            if party:
                return str(party)

    parties = contract.get("parties")
    if not isinstance(parties, list):
        inner = contract.get("contract") if isinstance(contract, dict) else None
        if isinstance(inner, dict):
            parties = inner.get("parties")
    if isinstance(parties, list):
        for p in parties:
            if isinstance(p, dict) and str(p.get("role", "")).lower() in {"borrower", "buyer", "tenant", "client"}:
                if p.get("name"):
                    return str(p.get("name"))
    return "unknown"


def _find_timeline_recursive(node: Any) -> Optional[str]:
    if isinstance(node, dict):
        terms = node.get("terms")
        if isinstance(terms, dict) and terms.get("timeline") is not None:
            val = str(terms.get("timeline")).strip()
            if val:
                return val
        for value in node.values():
            out = _find_timeline_recursive(value)
            if out is not None:
                return out
    elif isinstance(node, list):
        for item in node:
            out = _find_timeline_recursive(item)
            if out is not None:
                return out
    return None


def _status_for(now: datetime, deadline: Optional[datetime], completed: bool) -> str:
    if completed:
        return "completed"
    if deadline is None:
        return "pending"
    if now > deadline:
        return "overdue"
    # Due window: less than or equal reminder lead.
    if (deadline - now).total_seconds() <= REMINDER_LEAD_SECONDS:
        return "due"
    return "pending"


def _update_reputation_for_contract(record: Dict[str, Any]) -> None:
    phones = record.get("phones") or []
    if not isinstance(phones, list):
        return

    status = record.get("status", "pending")
    for phone in phones:
        rep = reputation.setdefault(phone, {"contracts_completed": 0, "breaches": 0, "reliability_score": 100.0})
        if status == "completed":
            rep["contracts_completed"] += 1
        elif status == "overdue":
            rep["breaches"] += 1
        total = rep["contracts_completed"] + rep["breaches"]
        rep["reliability_score"] = 100.0 if total == 0 else round((rep["contracts_completed"] / total) * 100.0, 2)


def _send_twilio_message(to_phone: str, body: str) -> Dict[str, Any]:
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        logger.info("Twilio credentials/from missing; skipping send to %s: %s", to_phone, body)
        return {"status": "skipped", "reason": "twilio_not_configured", "to": to_phone}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                url,
                data={
                    "To": to_phone,
                    "From": TWILIO_WHATSAPP_FROM,
                    "Body": body,
                },
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )
            resp.raise_for_status()
            data = resp.json()
            return {"status": "sent", "sid": data.get("sid"), "to": to_phone}
    except Exception as exc:
        logger.warning("Twilio send failed to %s: %s", to_phone, exc)
        return {"status": "error", "to": to_phone, "error": str(exc)}


def _notify(record: Dict[str, Any], event: str, body: str) -> None:
    notifications = record.setdefault("notifications", [])
    for phone in record.get("phones", []):
        result = _send_twilio_message(phone, body)
        notifications.append(
            {
                "event": event,
                "phone": phone,
                "result": result,
                "timestamp": _now().isoformat(),
            }
        )


def _scheduler_tick() -> None:
    now = _now()
    for record in contracts.values():
        previous = record.get("status", "pending")
        deadline = _parse_iso_or_now(record.get("deadline_at")) if record.get("deadline_at") else None
        completed = bool(record.get("completed", False))
        current = _status_for(now, deadline, completed)
        record["status"] = current

        if record.get("event_contract_created_sent") is not True:
            _notify(record, "contract_created", f"Contract {record.get('contract_id')} registered.")
            record["event_contract_created_sent"] = True

        if current == "due" and record.get("event_due_sent") is not True:
            _notify(
                record,
                "reminder_before_deadline",
                f"Reminder: contract {record.get('contract_id')} is due by {record.get('deadline_at')}",
            )
            record["event_due_sent"] = True

        if previous != "overdue" and current == "overdue" and record.get("event_overdue_sent") is not True:
            _notify(
                record,
                "overdue",
                f"Overdue: contract {record.get('contract_id')} missed deadline {record.get('deadline_at')}",
            )
            record["event_overdue_sent"] = True

        _update_reputation_for_contract(record)


async def _scheduler_loop() -> None:
    while True:
        try:
            _scheduler_tick()
        except Exception as exc:
            logger.exception("Layer5 scheduler tick failed: %s", exc)
        await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(_scheduler_loop())


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "layer5"}


@app.post("/register_contract")
def register_contract(body: RegisterContractRequest) -> Dict[str, Any]:
    created_at = _parse_iso_or_now(body.timestamp)
    timeline = _extract_timeline(body.contract)
    deadline = _parse_timeline_to_deadline(created_at, timeline)

    record = {
        "contract_id": body.contract_id,
        "contract": body.contract,
        "created_at": created_at.isoformat(),
        "timeline_raw": timeline,
        "deadline_at": deadline.isoformat() if deadline else None,
        "status": "pending",
        "completed": False,
        "phones": _extract_party_phones(body.contract),
        "notifications": [],
        "disputes": [],
        "event_contract_created_sent": False,
        "event_due_sent": False,
        "event_overdue_sent": False,
    }
    contracts[body.contract_id] = record

    _scheduler_tick()

    return {
        "status": "registered",
        "contract_id": body.contract_id,
        "created_at": record["created_at"],
        "deadline_at": record["deadline_at"],
        "timeline_raw": record["timeline_raw"],
        "state": record["status"],
        "phones": record["phones"],
    }


@app.get("/status/{contract_id}")
def get_status(contract_id: str) -> Dict[str, Any]:
    record = contracts.get(contract_id)
    if not record:
        raise HTTPException(status_code=404, detail="contract not found")

    return {
        "contract_id": contract_id,
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "deadline_at": record.get("deadline_at"),
        "timeline_raw": record.get("timeline_raw"),
        "notifications": record.get("notifications", []),
        "phones": record.get("phones", []),
    }


@app.post("/dispute")
def dispute(body: DisputeRequest) -> Dict[str, Any]:
    record = contracts.get(body.contract_id)
    if not record:
        raise HTTPException(status_code=404, detail="contract not found")

    violating_party = _extract_violating_party(record.get("contract") or {})
    dispute_summary = f"Dispute opened for contract {body.contract_id}: {body.reason}"

    evidence_package = {
        "contract_id": body.contract_id,
        "snapshot": record.get("contract"),
        "status": record.get("status"),
        "deadline_at": record.get("deadline_at"),
        "notifications": record.get("notifications", []),
        "opened_at": _now().isoformat(),
    }

    event = {
        "summary": dispute_summary,
        "violating_party": violating_party,
        "evidence_package": evidence_package,
    }
    record.setdefault("disputes", []).append(event)

    return {
        "contract_id": body.contract_id,
        "dispute_summary": dispute_summary,
        "violating_party": violating_party,
        "evidence_package": evidence_package,
    }


@app.get("/reputation/{phone}")
def get_reputation(phone: str) -> Dict[str, Any]:
    rep = reputation.get(phone)
    if not rep:
        rep = {"contracts_completed": 0, "breaches": 0, "reliability_score": 100.0}

    return {
        "phone": phone,
        "contracts_completed": rep.get("contracts_completed", 0),
        "breaches": rep.get("breaches", 0),
        "reliability_score": rep.get("reliability_score", 100.0),
    }
