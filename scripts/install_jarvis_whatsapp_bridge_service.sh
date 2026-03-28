#!/usr/bin/env bash
# Instala y arranca el bridge como servicio systemd (rutas /walogout, /admin/whatsapp, /webhook).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="${ROOT}/systemd/jarvis-whatsapp-bridge.service"
UNIT_DST="/etc/systemd/system/jarvis-whatsapp-bridge.service"

if [[ -f "${ROOT}/.env" ]]; then
  if ! grep -qE '^[[:space:]]*AGENT_SECRET=[^[:space:]]' "${ROOT}/.env" 2>/dev/null; then
    echo "AVISO: definí AGENT_SECRET en ${ROOT}/.env (el panel /admin/whatsapp lo necesita para cerrar sesión Evolution)." >&2
  fi
else
  echo "AVISO: no hay ${ROOT}/.env — copiá .env.example y completá." >&2
fi

if [[ ! -f "${UNIT_SRC}" ]]; then
  echo "No existe ${UNIT_SRC}" >&2
  exit 1
fi

if [[ ! -x "${ROOT}/venv/bin/uvicorn" ]] && [[ -x "${ROOT}/.venv-bridge/bin/uvicorn" ]]; then
  echo "Ajustando ExecStart a .venv-bridge (no hay venv/bin/uvicorn)..."
  sed 's|venv/bin/uvicorn|.venv-bridge/bin/uvicorn|g' "${UNIT_SRC}" | sudo tee "${UNIT_DST}" >/dev/null
else
  sudo cp -a "${UNIT_SRC}" "${UNIT_DST}"
fi

# Sustituir WorkingDirectory si ROOT no es /root/telegram-bot
if [[ "${ROOT}" != "/root/telegram-bot" ]]; then
  sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=${ROOT}|g" "${UNIT_DST}"
  sudo sed -i "s|EnvironmentFile=.*|EnvironmentFile=-${ROOT}/.env|g" "${UNIT_DST}"
  sudo sed -i "s|/root/telegram-bot|${ROOT}|g" "${UNIT_DST}"
fi

sudo systemctl daemon-reload
sudo systemctl enable jarvis-whatsapp-bridge.service
# Si quedó un uvicorn huérfano (no systemd) ocupando 8766, el restart no lo mata y el panel da 404.
echo "Liberando puerto 8766 antes de arrancar…"
sudo systemctl stop jarvis-whatsapp-bridge.service 2>/dev/null || true
sudo fuser -k 8766/tcp 2>/dev/null || true
sleep 1
sudo systemctl start jarvis-whatsapp-bridge.service
sleep 1
sudo systemctl --no-pager -l status jarvis-whatsapp-bridge.service || true
echo ""
echo "Probar rutas:"
curl -sS -o /dev/null -w "GET /status -> %{http_code}\n" "http://127.0.0.1:8766/status" || true
curl -sS "http://127.0.0.1:8766/openapi.json" | head -c 5000 | grep -q walogout && echo "OK: OpenAPI incluye walogout" || echo "AVISO: no aparece walogout en OpenAPI (¿código viejo?)"
if [[ -x "${ROOT}/scripts/verify_bridge_health.sh" ]]; then
  echo ""
  "${ROOT}/scripts/verify_bridge_health.sh" "http://127.0.0.1:8766" || echo "AVISO: verify_bridge_health falló (servicio recién arrancando: reintentá en 5s)." >&2
fi
