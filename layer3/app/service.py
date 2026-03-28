from app.normalizer import normalize_deadline
from app.reasoning import (
    build_obligation,
    build_summary,
    check_consistency,
    run_simulations,
)
from types import SimpleNamespace


def _extract_flat_contract(payload: dict) -> dict:
    contract = payload.get("contract", {})
    parties = contract.get("parties", [])

    lender = None
    borrower = None
    for party in parties:
        if party.get("role") == "lender" and lender is None:
            lender = party.get("name")
        if party.get("role") == "borrower" and borrower is None:
            borrower = party.get("name")

    amount = contract.get("terms", {}).get("amount", {}).get("value")
    currency = contract.get("terms", {}).get("amount", {}).get("currency") or "INR"
    deadline = contract.get("terms", {}).get("timeline")
    normalized_deadline = normalize_deadline(deadline) if deadline is not None else deadline

    return {
        "lender": lender,
        "borrower": borrower,
        "amount": amount,
        "currency": currency,
        "deadline": normalized_deadline,
    }


def reason_contract(payload: dict) -> dict:
    flat_contract = _extract_flat_contract(payload)

    contract_for_reasoning = SimpleNamespace(
        lender=flat_contract.get("lender") or "",
        borrower=flat_contract.get("borrower") or "",
        amount=flat_contract.get("amount") if flat_contract.get("amount") is not None else 0,
        currency=flat_contract.get("currency") or "INR",
        deadline=flat_contract.get("deadline") if flat_contract.get("deadline") is not None else "",
    )

    obligation = build_obligation(contract_for_reasoning, contract_for_reasoning.deadline)
    summary = build_summary(contract_for_reasoning, contract_for_reasoning.deadline)
    simulations = run_simulations()
    consistency = check_consistency(contract_for_reasoning)

    if flat_contract.get("lender") is None or flat_contract.get("borrower") is None:
        consistency.valid = False
        if "missing party name" not in consistency.issues:
            consistency.issues.append("missing party name")

    return {
        "layer2": payload,
        "layer3": {
            "summary": summary,
            "obligation": obligation.model_dump(),
            "simulations": [simulation.model_dump() for simulation in simulations],
            "consistency": consistency.model_dump(),
        },
    }