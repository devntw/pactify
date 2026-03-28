#!/usr/bin/env bash
set -euo pipefail

# Optional overrides:
#   export LAYER1_IMAGE=gawah-layer1:v2
#   export LAYER2_IMAGE=gawah-layer2:v2
#   export OPENAI_BASE_URL=http://host.docker.internal:1234/v1
#   export OPENAI_API_KEY=lm-studio
#   export OPENAI_MODEL=<lm-studio-model-id>
#   export LLM_JSON_RESPONSE_FORMAT=false
#   export TWILIO_ACCOUNT_SID=...
#   export TWILIO_AUTH_TOKEN=...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

if [[ -z "${TWILIO_AUTH_TOKEN:-}" ]]; then
	echo "[gawah] Warning: TWILIO_AUTH_TOKEN is empty. WhatsApp audio media fetch will fail with 401."
	echo "[gawah] Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env or shell before startup."
fi

echo "[gawah] Building layer images..."
docker compose build layer1 layer2 layer3 layer4 layer5

echo "[gawah] Starting services..."
docker compose up -d

echo "[gawah] Service status:"
docker compose ps

echo "[gawah] Health check from layer1 -> layer2:"
docker compose exec -T layer1 sh -lc "python -c \"import urllib.request;print(urllib.request.urlopen('http://layer2:8001/health', timeout=10).read().decode())\""

echo "[gawah] Health check for layer3:"
docker compose exec -T layer3 sh -lc "python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8002/health', timeout=10).read().decode())\""

echo "[gawah] Health check for layer4:"
docker compose exec -T layer4 sh -lc "python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8003/health', timeout=10).read().decode())\""

echo "[gawah] Health check for layer5:"
docker compose exec -T layer5 sh -lc "python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8004/health', timeout=10).read().decode())\""

echo "[gawah] Ready. Expose Layer1 webhook with: ngrok http 8000"
