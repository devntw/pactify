"""POST /ingest — Layer 1 ingestion (text or audio)."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.schemas import IngestRequest, Layer1Response
from app.services.audio import convert_to_wav, decode_base64_audio
from app.services.speech import transcribe_audio

router = APIRouter()


def _safe_request_log(payload: dict[str, Any]) -> str:
    safe = dict(payload)
    b64 = safe.get("audio_base64")
    if isinstance(b64, str) and b64.strip():
        safe["audio_base64"] = f"<omitted len={len(b64)} chars>"
    return json.dumps(safe, ensure_ascii=False)


@router.post("", response_model=Layer1Response)
async def ingest(body: IngestRequest) -> Layer1Response:
    print(f"[vsce] incoming request: {_safe_request_log(body.model_dump())}")

    phone = body.phone.strip()
    has_audio = body.audio_base64 is not None and str(body.audio_base64).strip() != ""
    has_text = body.text is not None and str(body.text).strip() != ""

    audio_path = ""
    final_text = ""

    if has_audio:
        raw_path: str | None = None
        wav_path: str | None = None
        try:
            raw_path = decode_base64_audio(body.audio_base64 or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            wav_path = convert_to_wav(raw_path)
            audio_path = wav_path
        except RuntimeError as exc:
            if raw_path and os.path.isfile(raw_path):
                try:
                    os.remove(raw_path)
                except OSError:
                    pass
            raise HTTPException(
                status_code=500,
                detail={"message": str(exc), "error_code": "ffmpeg_failure"},
            ) from exc

        if raw_path and os.path.isfile(raw_path):
            try:
                os.remove(raw_path)
            except OSError:
                pass

        try:
            final_text = transcribe_audio(wav_path)
        except RuntimeError as exc:
            if wav_path and os.path.isfile(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
            raise HTTPException(
                status_code=500,
                detail={"message": str(exc), "error_code": "whisper_failure"},
            ) from exc

    elif has_text:
        final_text = (body.text or "").strip()
    else:
        # Validated by Pydantic, but keep defensive branch
        raise HTTPException(
            status_code=422,
            detail="At least one of 'text' or 'audio_base64' must be provided",
        )

    out = Layer1Response(
        phone=phone,
        text=final_text,
        audio_path=audio_path,
        status="success",
    )
    print(f"[vsce] layer1 output: {out.model_dump_json()}")
    return out
