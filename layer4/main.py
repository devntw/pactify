from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
import hashlib
import io
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
from pypdf import PdfReader

app = FastAPI()

# simple storage (temporary for hackathon)
database = {}
LAYER5_URL = os.getenv("LAYER5_URL", "http://layer5:8004/register_contract")
LAYER5_TIMEOUT_SECONDS = float(os.getenv("LAYER5_TIMEOUT_SECONDS", "10"))
PROOF_DIR = Path(os.getenv("LAYER4_PROOF_DIR", "/tmp/layer4_proofs"))
PROOF_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("LAYER4_DB_PATH", str(PROOF_DIR / "proofs.db")))


def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proofs (
                contract_id TEXT PRIMARY KEY,
                contract_hash TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                proof_json TEXT NOT NULL,
                pdf_path TEXT,
                pdf_sha256 TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_proofs_hash
            ON proofs(contract_hash)
            """
        )


@app.on_event("startup")
def startup() -> None:
    _init_db()

def normalize_contract(contract):
    normalized = {}
    for key in sorted(contract.keys()):
        value = contract[key]

        if isinstance(value, str):
            value = value.strip().lower()

        normalized[key] = value

    return normalized


def canonical_json(contract):
    return json.dumps(contract, separators=(',', ':'), sort_keys=True)


def generate_hash(canonical_string):
    return hashlib.sha256(canonical_string.encode()).hexdigest()


def generate_contract_id(hash_value):
    return hash_value[:12]


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upsert_proof_record(proof: dict, canonical: str) -> None:
    with _db_conn() as conn:
        conn.execute(
            """
            INSERT INTO proofs (
                contract_id,
                contract_hash,
                timestamp,
                canonical_json,
                proof_json,
                pdf_path,
                pdf_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_id) DO UPDATE SET
                contract_hash=excluded.contract_hash,
                timestamp=excluded.timestamp,
                canonical_json=excluded.canonical_json,
                proof_json=excluded.proof_json,
                pdf_path=excluded.pdf_path,
                pdf_sha256=excluded.pdf_sha256
            """,
            (
                proof.get("contract_id"),
                proof.get("hash"),
                proof.get("timestamp"),
                canonical,
                json.dumps(proof, ensure_ascii=False),
                proof.get("pdf_path"),
                proof.get("pdf_sha256"),
            ),
        )


def _get_proof_record(contract_id: str) -> dict:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM proofs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    return dict(row) if row else {}


@app.get("/")
def home():
    return {"message": "Contract Proof Service Running"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "layer4"}


@app.post("/generate-proof")
def generate_proof(contract: dict):
    normalized = normalize_contract(contract)
    canonical = canonical_json(normalized)
    hash_value = generate_hash(canonical)
    contract_id = generate_contract_id(hash_value)

    proof = {
        "contract_id": contract_id,
        "hash": hash_value,
        "timestamp": datetime.utcnow().isoformat(),
        "contract": normalized
    }

    database[contract_id] = proof

    pdf_path = _generate_pdf(proof)
    proof["pdf_path"] = str(pdf_path)
    proof["pdf_sha256"] = _hash_bytes(pdf_path.read_bytes())

    _upsert_proof_record(proof, canonical)

    layer5_result = _register_with_layer5(proof)
    proof["layer5_registration"] = layer5_result

    return proof


@app.post("/proof/generate")
def generate_proof_alias(contract: dict):
    return generate_proof(contract)


def _register_with_layer5(proof: dict) -> dict:
    if not LAYER5_URL.strip():
        return {"status": "skipped", "reason": "LAYER5_URL empty"}
    payload = {
        "contract_id": proof.get("contract_id"),
        "contract": proof.get("contract"),
        "timestamp": proof.get("timestamp"),
    }
    try:
        with httpx.Client(timeout=LAYER5_TIMEOUT_SECONDS) as client:
            resp = client.post(LAYER5_URL, json=payload)
            resp.raise_for_status()
            return {"status": "ok", "response": resp.json()}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _generate_pdf(proof: dict) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    contract_id = str(proof.get("contract_id", "unknown"))
    hash_value = str(proof.get("hash", ""))
    timestamp = str(proof.get("timestamp", ""))
    contract = proof.get("contract", {})
    payload_text = json.dumps(contract, indent=2, ensure_ascii=False)
    details = _extract_contract_details(contract)

    out_path = PROOF_DIR / f"{contract_id}.pdf"
    c = canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4
    y = height - 40

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "VSCE Agreement Proof")
    y -= 24
    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Contract ID: {contract_id}")
    y -= 14
    c.drawString(40, y, f"Timestamp: {timestamp}")
    y -= 14
    c.drawString(40, y, f"Hash: {hash_value}")
    y -= 20

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Agreement Details")
    y -= 16
    c.setFont("Helvetica", 10)
    for line in _build_details_lines(details):
        if y < 40:
            c.showPage()
            y = height - 40
            c.setFont("Helvetica", 10)
        for wrapped in _wrap_line(c, line, width - 80, "Helvetica", 10):
            if y < 40:
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 10)
            c.drawString(40, y, wrapped)
            y -= 12

    y -= 8
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Agreement Payload")
    y -= 16
    c.setFont("Courier", 8)
    for line in payload_text.splitlines():
        if y < 30:
            c.showPage()
            y = height - 40
            c.setFont("Courier", 8)
        c.drawString(40, y, line[:130])
        y -= 10

    c.save()
    return out_path


def _extract_contract_details(root: dict) -> dict:
    layer2 = root.get("layer2") if isinstance(root, dict) else {}
    if not isinstance(layer2, dict):
        layer2 = {}

    contract = layer2.get("contract") if isinstance(layer2.get("contract"), dict) else {}
    terms = contract.get("terms") if isinstance(contract.get("terms"), dict) else {}
    amount = terms.get("amount") if isinstance(terms.get("amount"), dict) else {}

    parties = contract.get("parties") if isinstance(contract.get("parties"), list) else []
    obligations = contract.get("obligations") if isinstance(contract.get("obligations"), list) else []

    return {
        "contract_type": contract.get("type") or "unknown",
        "subject": contract.get("subject") or "",
        "consideration": contract.get("consideration") or "",
        "amount_value": amount.get("value"),
        "amount_currency": amount.get("currency") or "INR",
        "timeline": terms.get("timeline") or "",
        "parties": parties,
        "obligations": obligations,
        "initiator_phone": layer2.get("initiator_phone") or "",
        "confirmer_phone": layer2.get("confirmer_phone") or "",
        "confirmation_status": layer2.get("status") or layer2.get("confirmation_status") or "",
    }


def _build_details_lines(details: dict) -> list[str]:
    lines = [
        f"Type: {details.get('contract_type') or 'unknown'}",
        f"Subject: {details.get('subject') or 'not provided'}",
        f"Consideration: {details.get('consideration') or 'not provided'}",
    ]

    amount_value = details.get("amount_value")
    amount_currency = details.get("amount_currency") or "INR"
    if amount_value is not None:
        lines.append(f"Amount: {amount_value} {amount_currency}")
    else:
        lines.append("Amount: not provided")

    lines.append(f"Timeline: {details.get('timeline') or 'not provided'}")

    parties = details.get("parties") or []
    if parties:
        lines.append("Parties:")
        for idx, party in enumerate(parties, start=1):
            if not isinstance(party, dict):
                continue
            role = party.get("role") or "party"
            name = party.get("name") or f"Party {idx}"
            lines.append(f"  - {name} ({role})")
    else:
        lines.append("Parties: not provided")

    obligations = details.get("obligations") or []
    if obligations:
        lines.append("Obligations:")
        for ob in obligations:
            if not isinstance(ob, dict):
                continue
            who = ob.get("party") or "unspecified party"
            duty = ob.get("duty") or "unspecified duty"
            lines.append(f"  - {who}: {duty}")
    else:
        lines.append("Obligations: not provided")

    initiator = details.get("initiator_phone") or "not provided"
    confirmer = details.get("confirmer_phone") or "not provided"
    status = details.get("confirmation_status") or "not provided"
    lines.append(f"Initiator Phone: {initiator}")
    lines.append(f"Confirmer Phone: {confirmer}")
    lines.append(f"Confirmation Status: {status}")

    return lines


def _wrap_line(c, text: str, max_width: float, font_name: str, font_size: int) -> list[str]:
    if not text:
        return [""]

    words = str(text).split()
    if not words:
        return [""]

    out: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if c.stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            out.append(current)
            current = word
    out.append(current)
    return out


@app.get("/verify/{contract_id}")
def verify_contract(contract_id: str):
    if contract_id in database:
        return {
            "valid": True,
            "proof": database[contract_id]
        }

    record = _get_proof_record(contract_id)
    if record:
        proof_data = json.loads(record.get("proof_json") or "{}")
        return {
            "valid": True,
            "proof": proof_data,
            "source": "sqlite",
        }

    return {"valid": False, "message": "Contract not found"}


@app.post("/verify-upload")
async def verify_uploaded_pdf(file: UploadFile = File(...)):
    uploaded = await file.read()
    if not uploaded:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    uploaded_pdf_sha = _hash_bytes(uploaded)
    parsed = _extract_contract_id_and_hash_from_pdf(uploaded)
    if not parsed:
        return {
            "valid": False,
            "reason": "Could not extract Contract ID/Hash from PDF",
            "uploaded_pdf_sha256": uploaded_pdf_sha,
        }

    contract_id, embedded_hash = parsed
    record = _get_proof_record(contract_id)
    if not record:
        return {
            "valid": False,
            "reason": "Contract ID not found in DB",
            "contract_id": contract_id,
            "embedded_hash": embedded_hash,
            "uploaded_pdf_sha256": uploaded_pdf_sha,
        }

    db_hash = record.get("contract_hash") or ""
    contract_hash_match = embedded_hash == db_hash
    exact_pdf_match = uploaded_pdf_sha == (record.get("pdf_sha256") or "")

    proof_data = json.loads(record.get("proof_json") or "{}")
    return {
        "valid": contract_hash_match,
        "contract_hash_match": contract_hash_match,
        "exact_pdf_match": exact_pdf_match,
        "contract_id": contract_id,
        "embedded_hash": embedded_hash,
        "db_hash": db_hash,
        "uploaded_pdf_sha256": uploaded_pdf_sha,
        "db_pdf_sha256": record.get("pdf_sha256"),
        "proof": proof_data,
    }


def _extract_contract_id_and_hash_from_pdf(pdf_bytes: bytes):
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return None

    text_parts = []
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            continue

    text = "\n".join(text_parts)
    contract_match = re.search(r"Contract\s*ID\s*:\s*([a-fA-F0-9]{12})", text)
    hash_match = re.search(r"Hash\s*:\s*([a-fA-F0-9]{64})", text)
    if not contract_match or not hash_match:
        return None
    return contract_match.group(1).lower(), hash_match.group(1).lower()


@app.get("/proof-file/{contract_id}.pdf")
def get_proof_file(contract_id: str):
    file_path = PROOF_DIR / f"{contract_id}.pdf"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Proof PDF not found")
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=f"{contract_id}.pdf",
    )