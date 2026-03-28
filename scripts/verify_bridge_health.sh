#!/usr/bin/env bash
# Comprueba que el bridge en 8766 está vivo y que el endpoint de logout Evolution existe.
# No usa tu AGENT_SECRET real: solo envía un token inválido y espera 403 JSON.
set -euo pipefail
BASE="${1:-http://127.0.0.1:8766}"
BASE="${BASE%/}"

echo "== Verificando ${BASE} =="

code="$(curl -sS -o /dev/null -w "%{http_code}" "${BASE}/status" || echo "000")"
if [[ "${code}" != "200" ]]; then
  echo "ERROR: GET /status -> HTTP ${code} (esperado 200)" >&2
  exit 1
fi
echo "OK: GET /status -> 200"

tmp="$(mktemp)"
http_code="$(curl -sS -o "${tmp}" -w "%{http_code}" -X POST "${BASE}/admin/whatsapp/api/evolution-logout" \
  -H "Content-Type: application/json" \
  -d '{"token":"__verify_invalid_token__"}')"
json_part="$(cat "${tmp}")"
rm -f "${tmp}"
if [[ "${http_code}" != "403" ]]; then
  echo "ERROR: POST evolution-logout con token falso -> HTTP ${http_code} (esperado 403)" >&2
  echo "Cuerpo: ${json_part}" >&2
  if [[ "${http_code}" == "404" ]]; then
    echo "Pista: 404 = proceso viejo o no es whatsapp_bridge. Subí el repo actual y ejecutá: sudo ./scripts/deploy_bridge.sh" >&2
  fi
  exit 1
fi
if ! echo "${json_part}" | grep -q '"ok"'; then
  echo "ERROR: respuesta no parece JSON de logout" >&2
  exit 1
fi
echo "OK: POST /admin/whatsapp/api/evolution-logout rechaza token inválido (403)"

echo ""
echo "Listo: el bridge está actualizado y el panel web puede usar logout/QR."
