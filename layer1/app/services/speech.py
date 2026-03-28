import whisper
from app.config import WHISPER_MODEL

# Load model once
print(f"[vsce] loading whisper model: {WHISPER_MODEL}")
model = whisper.load_model(WHISPER_MODEL)


def transcribe_audio(file_path: str) -> str:
    result = model.transcribe(
        file_path,
        fp16=False,
        language="en"  # force English
    )

    text = result.get("text", "").strip()

    print("[vsce] transcription:", text)

    return text
