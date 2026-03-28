import logging
import os
from typing import Any, Dict

import httpx
from fastapi import FastAPI

from app.service import reason_contract


logger = logging.getLogger(__name__)
app = FastAPI(title="VSCE Layer 3", version="1.0.0")

_latest: Dict[str, Any] = {}
LAYER4_URL = os.getenv("LAYER4_URL", "http://layer4:8003/generate-proof")
LAYER4_TIMEOUT_SECONDS = float(os.getenv("LAYER4_TIMEOUT_SECONDS", "10"))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "layer3"}


@app.post("/contract/reason")
def contract_reason(payload: dict) -> dict:
    return reason_contract(payload)


@app.post("/ingest")
def ingest(payload: dict) -> dict:
    layer2_result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(layer2_result, dict):
        layer2_result = payload if isinstance(payload, dict) else {}

    reasoning = reason_contract(layer2_result)
    proof = _generate_proof(reasoning)

    out = {
        "phone": payload.get("phone") if isinstance(payload, dict) else None,
        "text": payload.get("text") if isinstance(payload, dict) else None,
        "audio_path": payload.get("audio_path") if isinstance(payload, dict) else None,
        "layer2": layer2_result,
        "layer3": reasoning.get("layer3", {}),
        "layer4": proof,
    }
    _latest.clear()
    _latest.update(out)
    return {"status": "accepted", "result": out}


@app.get("/last")
def last() -> dict:
    if not _latest:
        return {"status": "empty"}
    return {"status": "ok", "payload": _latest}


def _generate_proof(data: dict) -> dict:
    if not LAYER4_URL.strip():
        return {"status": "skipped", "reason": "LAYER4_URL empty"}

    try:
        with httpx.Client(timeout=LAYER4_TIMEOUT_SECONDS) as client:
            resp = client.post(LAYER4_URL, json=data)
            resp.raise_for_status()
            return {"status": "ok", "proof": resp.json()}
    except Exception as exc:
        logger.warning("Layer4 proof generation failed (%s): %s", LAYER4_URL, exc)
        return {"status": "error", "error": str(exc)}