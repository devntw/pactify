"""Pydantic request/response models."""

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class IngestRequest(BaseModel):
    phone: str = Field(..., min_length=1, description="Caller phone identifier")
    text: Optional[str] = None
    audio_base64: Optional[str] = None

    @model_validator(mode="after")
    def require_text_or_audio(self) -> "IngestRequest":
        has_text = self.text is not None and str(self.text).strip() != ""
        has_audio = self.audio_base64 is not None and str(self.audio_base64).strip() != ""
        if not has_text and not has_audio:
            raise ValueError("At least one of 'text' or 'audio_base64' must be provided")
        return self


class Layer1Response(BaseModel):
    phone: str
    text: str
    audio_path: str
    status: str = "success"
