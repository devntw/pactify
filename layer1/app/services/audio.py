import os
import base64
import subprocess
import uuid
import re

import requests

TEMP_PATH = "/tmp/audio_input"

os.makedirs(TEMP_PATH, exist_ok=True)


def download_audio(media_url: str) -> str:
    """
    Download voice media from Twilio. Use TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN
    for HTTP Basic auth when Twilio requires it.
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    auth = (sid, token) if sid and token else None
    r = requests.get(media_url, auth=auth, timeout=120)
    r.raise_for_status()
    ext = ".ogg"
    ct = (r.headers.get("Content-Type") or "").lower()
    if "mpeg" in ct or "mp3" in ct:
        ext = ".mp3"
    elif "wav" in ct:
        ext = ".wav"
    elif "m4a" in ct or "mp4" in ct:
        ext = ".m4a"
    path = os.path.join(TEMP_PATH, f"twilio_{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def download_audio_from_twilio(
    media_url: str,
    account_sid: str = "",
    auth_token: str = "",
) -> str:
    """Download Twilio-hosted media URL using robust SID/token fallbacks."""
    sid = (account_sid or "").strip() or os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = (auth_token or "").strip() or os.getenv("TWILIO_AUTH_TOKEN", "").strip()

    if not sid:
        m = re.search(r"/Accounts/([^/]+)/", media_url)
        if m:
            sid = m.group(1)

    if not token:
        raise Exception(
            "Twilio media auth missing: set TWILIO_AUTH_TOKEN in layer1 environment"
        )

    if not sid:
        raise Exception(
            "Twilio media auth missing: provide AccountSid in webhook or set TWILIO_ACCOUNT_SID"
        )

    r = requests.get(media_url, auth=(sid, token), timeout=120)
    if r.status_code == 401:
        raise Exception(
            "Twilio media fetch unauthorized (401). Verify TWILIO_AUTH_TOKEN/TWILIO_ACCOUNT_SID"
        )
    r.raise_for_status()

    ext = ".ogg"
    ct = (r.headers.get("Content-Type") or "").lower()
    if "mpeg" in ct or "mp3" in ct:
        ext = ".mp3"
    elif "wav" in ct:
        ext = ".wav"
    elif "m4a" in ct or "mp4" in ct:
        ext = ".m4a"

    path = os.path.join(TEMP_PATH, f"twilio_{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def decode_base64_audio(audio_base64: str) -> str:
    """
    Decode base64 audio and save to file
    """
    try:
        file_path = os.path.join(TEMP_PATH, "input_audio.wav")

        audio_bytes = base64.b64decode(audio_base64)

        with open(file_path, "wb") as f:
            f.write(audio_bytes)

        return file_path

    except Exception as e:
        raise Exception(f"Failed to decode base64 audio: {str(e)}")


def convert_to_wav(input_path: str) -> str:
    """
    Convert any audio format to 16kHz mono WAV for Whisper
    """
    output_path = os.path.join(TEMP_PATH, "converted.wav")

    command = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        output_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if result.returncode != 0:
        raise Exception(f"FFmpeg error: {result.stderr.decode()}")

    # Debug: check file size
    print("Audio file size:", os.path.getsize(output_path))

    return output_path
