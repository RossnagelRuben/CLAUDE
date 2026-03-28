#!/usr/bin/env bash
# Despliegue completo en el VPS: instala/reinicia systemd y verifica rutas.
# Uso: sudo ./scripts/deploy_bridge.sh
#      sudo ./scripts/deploy_bridge.sh --skip-verify   (solo reinicio)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKIP_VERIFY=0
for a in "$@"; do
  if [[ "$a" == "--skip-verify" ]]; then SKIP_VERIFY=1; fi
done

cd "${ROOT}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Ejecutá con sudo: sudo $0 $*" >&2
  exit 1
fi

if [[ ! -f "${ROOT}/whatsapp_bridge.py" ]] || [[ ! -f "${ROOT}/evolution_qr.py" ]]; then
  echo "No encuentro whatsapp_bridge.py en ${ROOT}" >&2
  exit 1
fi

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "AVISO: no hay ${ROOT}/.env — copiá .env.example a .env y completá variables." >&2
else
  if ! grep -qE '^[[:space:]]*AGENT_SECRET=[^[:space:]]' "${ROOT}/.env" 2>/dev/null; then
    echo "AVISO: AGENT_SECRET no está definido en .env — el logout del panel no funcionará." >&2
  fi
  if ! grep -qE '^[[:space:]]*EVOLUTION_API_URL=[^[:space:]]' "${ROOT}/.env" 2>/dev/null; then
    echo "AVISO: EVOLUTION_API_URL vacío — Evolution no estará configurado." >&2
  fi
fi

# venv mínimo
if [[ -x "${ROOT}/venv/bin/python3" ]] || [[ -x "${ROOT}/venv/bin/python" ]]; then
  :
elif [[ -x "${ROOT}/.venv-bridge/bin/python3" ]] || [[ -x "${ROOT}/.venv-bridge/bin/python" ]]; then
  :
else
  echo "ERROR: no hay venv/bin/python ni .venv-bridge/bin/python. Creá el entorno antes:" >&2
  echo "  cd ${ROOT} && python3 -m venv venv && ./venv/bin/pip install -U pip && ./venv/bin/pip install -r requirements-bridge.txt" >&2
  exit 1
fi

echo "== Instalando unidad systemd =="
bash "${ROOT}/scripts/install_jarvis_whatsapp_bridge_service.sh"

if [[ "${SKIP_VERIFY}" -eq 0 ]]; then
  echo ""
  echo "== Verificación HTTP (localhost) =="
  sleep 2
  bash "${ROOT}/scripts/verify_bridge_health.sh" "http://127.0.0.1:8766" || {
    echo "" >&2
    echo "La verificación falló. Revisá: journalctl -u jarvis-whatsapp-bridge -n 80 --no-pager" >&2
    exit 1
  }
fi

echo ""
echo "Hecho. Panel: http://TU_IP:8766/admin/whatsapp"
