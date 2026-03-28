#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <phone> <audio_file_path>"
  exit 1
fi

PHONE="$1"
AUDIO_FILE="$2"

if [[ ! -f "$AUDIO_FILE" ]]; then
  echo "Audio file not found: $AUDIO_FILE"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

B64="$(base64 < "$AUDIO_FILE" | tr -d '\n')"

echo "[audio-test] Sending audio to Layer1 /ingest ..."
INGEST_JSON="$(curl -s -X POST http://127.0.0.1:8000/ingest \
  -H 'Content-Type: application/json' \
  -d "{\"phone\":\"$PHONE\",\"audio_base64\":\"$B64\"}")"
echo "$INGEST_JSON"

TEXT="$(echo "$INGEST_JSON" | sed -n 's/.*"text":"\([^"]*\)".*/\1/p' | sed 's/\\n/ /g' | sed 's/\\"/"/g')"
AUDIO_PATH="$(echo "$INGEST_JSON" | sed -n 's/.*"audio_path":"\([^"]*\)".*/\1/p')"

if [[ -z "$TEXT" ]]; then
  echo "[audio-test] No transcribed text found from Layer1 output."
  exit 1
fi

echo "[audio-test] Posting transcript to Layer2 /process ..."
PROCESS_JSON="$(curl -s -X POST http://127.0.0.1:8001/process \
  -H 'Content-Type: application/json' \
  -d "{\"phone\":\"$PHONE\",\"text\":\"$TEXT\",\"audio_path\":\"$AUDIO_PATH\"}")"
echo "$PROCESS_JSON"

echo "[audio-test] Latest payload at Layer3 /last ..."
curl -s http://127.0.0.1:8002/last || true
echo
