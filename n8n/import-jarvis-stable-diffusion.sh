#!/usr/bin/env bash
# Reimporta el agente "Jarvis - Stable Diffusion" en el contenedor Docker `n8n`.
# Uso: desde el host donde corre Docker:  bash n8n/import-jarvis-stable-diffusion.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JSON="$ROOT/n8n/jarvis-stable-diffusion-agente.json"
docker cp "$JSON" n8n:/tmp/jarvis-stable-diffusion-agente.json
docker exec n8n n8n import:workflow --input=/tmp/jarvis-stable-diffusion-agente.json
docker exec n8n n8n update:workflow --id=4 --active=true
docker restart n8n
echo "Listo. Esperá ~10s y recargá http://TU_IP:5678/home/chat/workflow-agents"
