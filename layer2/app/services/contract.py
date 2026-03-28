import re
from typing import Any, Dict, List, Optional, Set

from app.models.schemas import ContractPayload


def is_smalltalk_or_greeting(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True

    # Common openers that do not carry contract details.
    smalltalk = {
        "hi",
        "hii",
        "hiii",
        "hello",
        "hey",
        "yo",
        "hola",
        "salam",
        "assalam",
        "good morning",
        "good afternoon",
        "good evening",
        "ok",
        "okay",
        "thanks",
        "thank you",
    }
    if t in smalltalk:
        return True

    # A very short alpha-only message is usually not contract content.
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) <= 2 and all(re.fullmatch(r"[a-zA-Z]+", w) for w in words):
        return True

    return False


def has_contract_signal(contract: Dict[str, Any]) -> bool:
    parties = contract.get("parties") or []
    non_empty = [p for p in parties if p and _party_has_signal(p)]
    if len(non_empty) >= 2:
        return True

    if (contract.get("subject") or "").strip():
        return True
    if (contract.get("consideration") or "").strip():
        return True

    terms = contract.get("terms") or {}
    amount = terms.get("amount") or {}
    if amount.get("value") is not None:
        return True
    timeline = terms.get("timeline")
    if isinstance(timeline, str) and timeline.strip():
        return True
    if timeline and not isinstance(timeline, str):
        return True

    return False


def merge_contract(
    old: Optional[Dict[str, Any]], new: Dict[str, Any]
) -> Dict[str, Any]:
    if not old:
        return _normalize_contract(new)
    merged = _merge_recursive(old, new)
    return _normalize_contract(merged)


def _merge_recursive(old: Any, new: Any) -> Any:
    if new is None:
        return old
    if isinstance(new, dict) and isinstance(old, dict):
        keys = set(old) | set(new)
        out: Dict[str, Any] = {}
        for k in keys:
            ov, nv = old.get(k), new.get(k)
            if nv is None:
                out[k] = ov
            elif k == "type" and isinstance(ov, str) and isinstance(nv, str):
                # Do not downgrade a known contract type to "unknown" on follow-up messages.
                out[k] = ov if ov != "unknown" and nv == "unknown" else nv
            elif isinstance(nv, dict) and isinstance(ov, dict):
                out[k] = _merge_recursive(ov, nv)
            elif isinstance(nv, list) and k == "parties":
                if _is_generic_parties(nv) and not _is_generic_parties(ov or []):
                    out[k] = ov
                else:
                    out[k] = _merge_party_lists(ov or [], nv)
            elif isinstance(nv, list) and k == "obligations":
                out[k] = _merge_obligation_lists(ov or [], nv)
            elif isinstance(nv, list) and k == "conditions":
                out[k] = _merge_conditions(ov or [], nv)
            else:
                out[k] = nv
        return out
    if isinstance(new, list) and isinstance(old, list):
        return new if new else old
    return new


def _merge_party_lists(old: List[Any], new: List[Any]) -> List[Any]:
    if not new:
        return old
    max_len = max(len(old), len(new))
    result: List[Any] = []
    for i in range(max_len):
        o = old[i] if i < len(old) else None
        n = new[i] if i < len(new) else None
        if n is None:
            result.append(o)
        elif isinstance(n, dict) and isinstance(o, dict):
            merged: Dict[str, Any] = {}
            for key in set(o) | set(n):
                nv, ov = n.get(key), o.get(key)
                merged[key] = nv if nv is not None else ov
            result.append(merged)
        else:
            result.append(n)
    return result


def _merge_obligation_lists(old: List[Any], new: List[Any]) -> List[Any]:
    if not new:
        return old
    max_len = max(len(old), len(new))
    result: List[Any] = []
    for i in range(max_len):
        o = old[i] if i < len(old) else None
        n = new[i] if i < len(new) else None
        if n is None:
            result.append(o)
        elif isinstance(n, dict) and isinstance(o, dict):
            merged: Dict[str, Any] = {}
            for key in set(o) | set(n):
                nv, ov = n.get(key), o.get(key)
                merged[key] = nv if nv is not None else ov
            result.append(merged)
        else:
            result.append(n)
    return result


def _merge_conditions(old: List[str], new: List[str]) -> List[str]:
    if new:
        seen: Set[str] = set()
        out: List[str] = []
        for x in old + new:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out
    return old


def _is_generic_parties(parties: List[Any]) -> bool:
    if not parties:
        return True
    roles = []
    for p in parties:
        if not isinstance(p, dict):
            continue
        role = (p.get("role") or "").strip().lower()
        roles.append(role)
    if not roles:
        return True
    return set(roles).issubset({"party", "partner", ""})


def _normalize_contract(data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return ContractPayload.model_validate(data).model_dump()
    except Exception:
        return ContractPayload().model_dump()


def compute_missing_fields(contract: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    parties = contract.get("parties") or []
    non_empty_parties = [p for p in parties if p and _party_has_signal(p)]
    if len(non_empty_parties) < 2:
        missing.append("second_party")
    subj = (contract.get("subject") or "").strip()
    cons = (contract.get("consideration") or "").strip()
    if not subj and not cons:
        missing.append("subject_or_consideration")
    terms = contract.get("terms") or {}
    amt = terms.get("amount") or {}
    if amt.get("value") is None:
        missing.append("amount")
    tl = (terms.get("timeline") or "").strip() if isinstance(terms.get("timeline"), str) else terms.get("timeline")
    if not tl:
        missing.append("timeline")
    return missing


def _party_has_signal(p: Any) -> bool:
    if not isinstance(p, dict):
        return False
    n = p.get("name")
    r = p.get("role")
    return bool((n and str(n).strip()) or (r and str(r).strip()))


def is_contract_complete(contract: Dict[str, Any]) -> bool:
    parties = contract.get("parties") or []
    non_empty = [p for p in parties if p and _party_has_signal(p)]
    if len(non_empty) < 2:
        return False
    subj = (contract.get("subject") or "").strip()
    cons = (contract.get("consideration") or "").strip()
    return bool(subj or cons)


def next_question_for(missing_fields: List[str]) -> str:
    priority = [
        ("second_party", "Who is the other person involved?"),
        ("subject_or_consideration", "What is being agreed — the subject or the payment?"),
        ("amount", "What is the amount?"),
        ("timeline", "When should this be completed?"),
    ]
    for key, q in priority:
        if key in missing_fields:
            return q
    return ""


def rule_based_extract(text: str) -> Dict[str, Any]:
    t = (text or "").lower().strip()
    ctype = "unknown"
    if any(w in t for w in ("lend", "loan", "borrow", "debt")):
        ctype = "loan"
    elif any(w in t for w in ("sell", "sale", "buy", "purchase")):
        ctype = "sale"
    elif any(w in t for w in ("rent", "lease", "tenant")):
        ctype = "rent"
    elif any(w in t for w in ("service", "work for", "consult")):
        ctype = "service"
    elif "partner" in t or "partnership" in t:
        ctype = "partnership"

    amount_val: Any = None
    m = re.search(
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:rupees?|inr|rs\.?|₹)\b",
        text or "",
        re.IGNORECASE,
    )
    if m:
        raw = m.group(1).replace(",", "")
        try:
            amount_val = float(raw)
            if amount_val.is_integer():
                amount_val = int(amount_val)
        except ValueError:
            amount_val = None
    if amount_val is None:
        nums = re.findall(r"\b(\d+(?:\.\d+)?)\b", text or "")
        for n in nums:
            try:
                v = float(n)
                if v > 0:
                    amount_val = int(v) if v.is_integer() else v
                    break
            except ValueError:
                continue

    timeline_val = _extract_timeline_text(text or "")

    parties: List[Dict[str, Any]] = []
    if ctype == "loan":
        parties = [
            {"name": None, "role": "lender"},
            {"name": None, "role": "borrower"},
        ]
    elif ctype == "sale":
        parties = [
            {"name": None, "role": "seller"},
            {"name": None, "role": "buyer"},
        ]
    elif ctype == "rent":
        parties = [
            {"name": None, "role": "landlord"},
            {"name": None, "role": "tenant"},
        ]
    elif ctype == "service":
        parties = [
            {"name": None, "role": "provider"},
            {"name": None, "role": "client"},
        ]
    elif ctype == "partnership":
        parties = [
            {"name": None, "role": "partner"},
            {"name": None, "role": "partner"},
        ]
    else:
        parties = [
            {"name": None, "role": "party"},
            {"name": None, "role": "party"},
        ]

    subject: Optional[str] = _extract_subject_text(text or "", ctype, amount_val)
    consideration: Optional[str] = None
    if amount_val is not None:
        consideration = f"{amount_val} INR"

    return {
        "type": ctype,
        "parties": parties,
        "subject": subject,
        "consideration": consideration,
        "terms": {
            "amount": {"value": amount_val, "currency": "INR"},
            "timeline": timeline_val,
            "conditions": [],
        },
        "obligations": [],
    }


def _extract_timeline_text(text: str) -> Optional[str]:
    s = (text or "").strip()
    if not s:
        return None
    lower = s.lower()

    simple = {
        "today",
        "tomorrow",
        "tonight",
        "next week",
        "next month",
        "next year",
    }
    if lower in simple:
        return s

    if re.search(r"\b(in|within|after)\s+\d+\s+(minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\b", lower):
        return s

    if re.search(r"\b(on|by)\s+\d{1,2}(st|nd|rd|th)?\b", lower):
        return s

    if re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", lower):
        return s

    if re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)(day)?\b", lower):
        return s

    return None


def _extract_subject_text(text: str, ctype: str, amount_val: Any) -> Optional[str]:
    s = (text or "").strip()
    if not s:
        return None

    # Avoid replacing useful prior subject with a pure follow-up like "tomorrow".
    if _extract_timeline_text(s) is not None and len(s.split()) <= 4:
        return None

    if ctype != "unknown" or amount_val is not None:
        return s[:200]

    if len(s.split()) >= 5:
        return s[:200]

    return None
