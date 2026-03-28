import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.config import settings
from app.services.contract import rule_based_extract

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a legal contract extraction engine.
Extract structured contract data from imperfect human speech.
Return ONLY valid JSON. No explanation.

The JSON must match this shape exactly (use null for unknown scalar fields, [] for empty lists):
{
  "type": "loan" | "sale" | "service" | "rent" | "partnership" | "unknown",
  "parties": [{"name": string | null, "role": string | null}],
  "subject": string | null,
  "consideration": string | null,
  "terms": {
    "amount": {"value": number | null, "currency": "INR"},
    "timeline": string | null,
    "conditions": string[]
  },
  "obligations": [{"party": string | null, "duty": string}]
}

Example:
Input:
"I sell you 2 goats for 5000 rupees"

Output:
{
  "type": "sale",
  "parties": [
    {"name": null, "role": "seller"},
    {"name": null, "role": "buyer"}
  ],
  "subject": "2 goats",
  "consideration": "5000 INR",
  "terms": {
    "amount": {"value": 5000, "currency": "INR"},
    "timeline": null,
    "conditions": []
  },
  "obligations": []
}
"""


def _openai_client() -> Optional[OpenAI]:
    """OpenAI cloud, or LM Studio / other OpenAI-compatible local server."""
    base = (settings.openai_base_url or "").strip()
    key = (settings.openai_api_key or "").strip()
    if base:
        api_key = key or "lm-studio"
        return OpenAI(
            base_url=base.rstrip("/"),
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
        )
    if key:
        return OpenAI(api_key=key, timeout=settings.llm_timeout_seconds)
    return None


def extract_contract_with_llm(text: str, audio_path: str) -> Dict[str, Any]:
    client = _openai_client()
    if client is None:
        logger.warning(
            "No LLM configured (set OPENAI_BASE_URL for LM Studio or OPENAI_API_KEY for OpenAI); "
            "using rule-based extraction"
        )
        return _enrich_missing_fields(rule_based_extract(text), text)

    user_content = f"Transcript (may be imperfect):\n{text}\n"
    if audio_path:
        user_content += f"\naudio_path (reference only): {audio_path}\n"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    base_kwargs: Dict[str, Any] = {
        "model": settings.openai_model,
        "messages": messages,
        "temperature": 0.2,
    }

    try:
        if settings.llm_json_response_format:
            try:
                completion = client.chat.completions.create(
                    **base_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                logger.warning(
                    "LLM json_object mode failed (%s); retrying without (typical for local servers)",
                    e,
                )
                completion = client.chat.completions.create(**base_kwargs)
        else:
            completion = client.chat.completions.create(**base_kwargs)

        raw = completion.choices[0].message.content or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            fuzzy = extract_json_object_fuzzy(raw)
            if not fuzzy:
                raise
            data = fuzzy
        return _coerce_contract(data, text)
    except Exception as e:
        logger.exception("LLM extraction failed: %s", e)
        return _enrich_missing_fields(rule_based_extract(text), text)


def _coerce_contract(data: Any, source_text: str = "") -> Dict[str, Any]:
    if not isinstance(data, dict):
        return _enrich_missing_fields(rule_based_extract(source_text), source_text)
    out = {
        "type": data.get("type") or "unknown",
        "parties": _coerce_parties(data.get("parties")),
        "subject": data.get("subject"),
        "consideration": data.get("consideration"),
        "terms": _coerce_terms(data.get("terms")),
        "obligations": _coerce_obligations(data.get("obligations")),
    }
    if out["type"] not in (
        "loan",
        "sale",
        "service",
        "rent",
        "partnership",
        "unknown",
    ):
        out["type"] = "unknown"
    return _enrich_missing_fields(out, source_text)


def _enrich_missing_fields(contract: Dict[str, Any], source_text: str) -> Dict[str, Any]:
    text = (source_text or "").strip()

    # Prefer a concrete subject over null/empty values for downstream systems.
    if text and not (contract.get("subject") or "").strip():
        contract["subject"] = text[:200]

    parties = contract.get("parties")
    if not isinstance(parties, list):
        parties = []

    # Ensure two party slots exist for common bilateral contracts.
    if len(parties) < 2:
        defaults = _default_roles_for_type(contract.get("type") or "unknown")
        for i in range(len(parties), 2):
            parties.append({"name": None, "role": defaults[i]})

    extracted_names = _extract_party_names(text)
    for i, p in enumerate(parties):
        if not isinstance(p, dict):
            parties[i] = {"name": f"Party {i + 1}", "role": None}
            continue

        current_name = (p.get("name") or "").strip()
        if not current_name:
            if i < len(extracted_names):
                p["name"] = extracted_names[i]
            else:
                p["name"] = f"Party {i + 1}"

    contract["parties"] = parties
    return contract


def _extract_party_names(text: str) -> List[str]:
    if not text:
        return []

    patterns = [
        r"\b([A-Za-z][A-Za-z'\-]{1,})\s+will\s+(?:lend|loan|give|pay|receive)\b",
        r"\bfrom\s+([A-Za-z][A-Za-z'\-]{1,})\b",
        r"\bto\s+([A-Za-z][A-Za-z'\-]{1,})\b",
    ]

    found: List[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            name = m.group(1).strip()
            if not name:
                continue
            normalized = name[0].upper() + name[1:].lower()
            if normalized not in found:
                found.append(normalized)
            if len(found) >= 2:
                return found

    # Fallback: take first two alphabetic tokens that look like names.
    for token in re.findall(r"\b[A-Za-z][A-Za-z'\-]{1,}\b", text):
        normalized = token[0].upper() + token[1:].lower()
        if normalized.lower() in {
            "will",
            "and",
            "from",
            "to",
            "in",
            "on",
            "for",
            "return",
            "rupees",
            "loan",
            "lend",
            "receive",
            "days",
        }:
            continue
        if normalized not in found:
            found.append(normalized)
        if len(found) >= 2:
            break

    return found


def _default_roles_for_type(contract_type: str) -> List[str]:
    t = (contract_type or "unknown").lower()
    if t == "loan":
        return ["lender", "borrower"]
    if t == "sale":
        return ["seller", "buyer"]
    if t == "rent":
        return ["landlord", "tenant"]
    if t == "service":
        return ["provider", "client"]
    if t == "partnership":
        return ["partner", "partner"]
    return ["party", "party"]


def _coerce_parties(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: List[Dict[str, Any]] = []
    for p in raw:
        if isinstance(p, dict):
            result.append(
                {
                    "name": p.get("name"),
                    "role": p.get("role"),
                }
            )
    return result


def _coerce_terms(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    amt = raw.get("amount") if isinstance(raw.get("amount"), dict) else {}
    val = amt.get("value")
    if val is not None:
        try:
            val = float(val)
            if val == int(val):
                val = int(val)
        except (TypeError, ValueError):
            val = None
    conditions = raw.get("conditions")
    if not isinstance(conditions, list):
        conditions = []
    conditions = [str(c) for c in conditions if c is not None]
    return {
        "amount": {"value": val, "currency": "INR"},
        "timeline": raw.get("timeline"),
        "conditions": conditions,
    }


def _coerce_obligations(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for o in raw:
        if isinstance(o, dict) and o.get("duty") is not None:
            out.append(
                {
                    "party": o.get("party"),
                    "duty": str(o.get("duty", "")),
                }
            )
    return out


def extract_json_object_fuzzy(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None
