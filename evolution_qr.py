"""
Lógica compartida: QR de Evolution API + logout (usa EVOLUTION_* y AGENT_SECRET del .env).

Sirve a:
- ``qr_server.py`` (puerto propio, ej. 8099)
- ``whatsapp_bridge.py`` en ``/evolution/`` (mismo puerto que el panel, ej. 8766)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from whatsapp_debug_log import log_event, redact_jsonish

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

EVOLUTION_URL = (os.getenv("EVOLUTION_API_URL") or "http://localhost:8080").strip().rstrip("/")
EVOLUTION_KEY = (os.getenv("EVOLUTION_API_KEY") or "").strip()
INSTANCE = (os.getenv("EVOLUTION_INSTANCE") or "jarvis").strip()
AGENT_SECRET = (os.getenv("AGENT_SECRET") or "").strip()
# Si tu Evolution monta las rutas bajo un prefijo (raro), ej. /api → "/api"
_EVO_PATH_PREFIX = (os.getenv("EVOLUTION_HTTP_PATH_PREFIX") or "").strip().rstrip("/")
if _EVO_PATH_PREFIX and not _EVO_PATH_PREFIX.startswith("/"):
    _EVO_PATH_PREFIX = "/" + _EVO_PATH_PREFIX


def _evo_path(suffix: str) -> str:
    """suffix debe empezar con / (ej. /instance/connect/foo)."""
    if _EVO_PATH_PREFIX:
        return f"{_EVO_PATH_PREFIX}{suffix}"
    return suffix

# Prefijo de API en el bridge FastAPI (mismo origen que el panel).
API_PREFIX = "/evolution/api"
# Misma UI bajo /admin/whatsapp/... (útil si un proxy solo reenvía /admin/ al bridge).
ADMIN_EVOLUTION_QR_API_PREFIX = "/admin/whatsapp/evolution/api"


def _evo_request(method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    url = f"{EVOLUTION_URL}{_evo_path(path)}"
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("apikey", EVOLUTION_KEY)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            code = r.getcode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    except Exception as e:
        log_event(
            "evolution_http",
            {
                "method": method,
                "evo_path": path,
                "evo_base_url": EVOLUTION_URL,
                "error": str(e)[:500],
                "error_type": type(e).__name__,
            },
        )
        raise
    try:
        preview = redact_jsonish(raw.decode("utf-8", errors="replace"))
    except Exception:
        preview = "(no decode)"
    log_event(
        "evolution_http",
        {
            "method": method,
            "evo_path": path,
            "evo_path_prefix": _EVO_PATH_PREFIX or "",
            "evo_base_url": EVOLUTION_URL,
            "instance": INSTANCE,
            "apikey_set": bool(EVOLUTION_KEY),
            "http_status": code,
            "response_preview": preview[:2000],
        },
    )
    return code, raw


def fetch_connect_payload() -> dict:
    """GET /instance/connect/{instance} — devuelve QR o estado de la instancia."""
    enc = quote(INSTANCE, safe="")
    code, raw = _evo_request("GET", f"/instance/connect/{enc}")
    try:
        return {"http": code, "data": json.loads(raw.decode("utf-8", errors="replace"))}
    except Exception:
        return {"http": code, "raw": raw.decode("utf-8", errors="replace")[:500]}


def do_logout(evolution_instance: str | None = None) -> dict:
    """Cierra sesión WhatsApp en Evolution para ``evolution_instance`` o ``EVOLUTION_INSTANCE`` del .env."""
    name = (evolution_instance or INSTANCE or "").strip()
    if not name:
        return {"http": 0, "raw": "instance vacío", "data": None}
    enc = quote(name, safe="")
    code, raw = _evo_request("DELETE", f"/instance/logout/{enc}")
    try:
        return {"http": code, "data": json.loads(raw.decode("utf-8", errors="replace"))}
    except Exception:
        return {"http": code, "raw": raw.decode("utf-8", errors="replace")[:500]}


def fetch_evolution_instances() -> dict:
    """GET /instance/fetchInstances — lista instancias (nombre, estado, etc.)."""
    code, raw = _evo_request("GET", "/instance/fetchInstances")
    try:
        return {"http": code, "data": json.loads(raw.decode("utf-8", errors="replace"))}
    except Exception:
        return {"http": code, "raw": raw.decode("utf-8", errors="replace")[:1200]}


def _drop_sensitive(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: _drop_sensitive(v)
            for k, v in obj.items()
            if str(k).lower() not in ("apikey", "token", "hash")
        }
    if isinstance(obj, list):
        return [_drop_sensitive(x) for x in obj]
    return obj


def normalize_instances_list(data: object) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("instances", "instance", "data", "response"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict) and (
                "instanceName" in v or "name" in v or "instance" in v
            ):
                return [v]
    return []


def evolution_instances_for_panel() -> dict:
    """
    Resumen seguro para el panel: qué instancias hay y cuál está configurada en .env.
    """
    if not EVOLUTION_KEY:
        return {"ok": False, "detail": "Falta EVOLUTION_API_KEY en .env"}
    r = fetch_evolution_instances()
    http = r.get("http", 0)
    if http != 200:
        return {
            "ok": False,
            "detail": f"Evolution HTTP {http}",
            "raw": r.get("raw"),
            "evo_base_url": EVOLUTION_URL,
        }
    data = r.get("data")
    items = normalize_instances_list(data)
    safe = _drop_sensitive(items)
    if not isinstance(safe, list):
        safe = []
    return {
        "ok": True,
        "instances": safe,
        "configured_instance": INSTANCE,
        "hint": "La sesión «abierta» suele tener status/state open o connected. Cerrá con logout usando ese instanceName si difiere del .env.",
    }


def _as_img_src(b: str) -> str:
    """Convierte base64 crudo o data-URI a valor usable en <img src>."""
    b = (b or "").strip()
    if not b:
        return ""
    if b.startswith("data:image"):
        return b
    if "base64," in b:
        return b
    compact = re.sub(r"\s+", "", b)
    if len(compact) >= 40 and re.match(r"^[A-Za-z0-9+/]+=*$", compact):
        return f"data:image/png;base64,{compact}"
    return ""


def _pairing_from_data(data: dict) -> str | None:
    for key in ("pairingCode", "pairing_code"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    qc = data.get("qrcode")
    if isinstance(qc, dict):
        v = qc.get("pairingCode") or qc.get("pairing_code")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _qr_src_from_connect_data(data: dict) -> str:
    """
    Evolution suele devolver el PNG en ``base64`` o dentro de ``qrcode.base64``,
    a veces anidado en ``response``.
    """
    if not isinstance(data, dict):
        return ""
    src = _as_img_src(str(data.get("base64") or ""))
    if src:
        return src
    qc = data.get("qrcode")
    if isinstance(qc, dict):
        src = _as_img_src(str(qc.get("base64") or ""))
        if src:
            return src
    resp = data.get("response")
    if isinstance(resp, dict):
        nested = _qr_src_from_connect_data(resp)
        if nested:
            return nested
    return ""


def _flatten_msg(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(_flatten_msg(x) for x in v).strip()
    return str(v).strip()


def _evolution_error_hint(http: int, result: dict) -> str:
    """Texto corto para el panel cuando Evolution no devuelve 2xx."""
    raw = str(result.get("raw") or "")
    data = result.get("data")
    msg = ""
    if isinstance(data, dict):
        msg = _flatten_msg(data.get("message") or data.get("error") or "")
        inner = data.get("response")
        if isinstance(inner, dict) and inner.get("message"):
            msg = f"{msg} {_flatten_msg(inner.get('message'))}".strip()
    base = f"Evolution HTTP {http}"
    if msg:
        return f"{base}: {msg[:280]}"
    if raw:
        return f"{base}: {raw[:280]}"
    return base


def build_api_qr_response() -> dict:
    out: dict = {"instance": INSTANCE}
    if not EVOLUTION_KEY:
        out["error"] = "Falta EVOLUTION_API_KEY en .env"
        out["hint"] = "Configurá el .env del servidor."
        log_event("evolution_qr", {"outcome": "missing_api_key"})
        return out

    try:
        payload = fetch_connect_payload()
        http = payload.get("http", 0)
        data = payload.get("data")
        if not isinstance(data, dict):
            out["error"] = f"Respuesta inválida (HTTP {http})"
            out["hint"] = str(payload.get("raw", ""))[:200]
            log_event(
                "evolution_qr",
                {"outcome": "connect_not_json", "connect_http": http},
            )
            return out

        img_src = _qr_src_from_connect_data(data)
        if img_src:
            out["base64"] = img_src
            pc = _pairing_from_data(data)
            out["pairingCode"] = pc
            out["hint"] = "Escaneá con WhatsApp (Dispositivos vinculados)."
            log_event(
                "evolution_qr",
                {"outcome": "qr_image_ok", "connect_http": http, "pairing": bool(pc)},
            )
            return out

        inst = data.get("instance")
        if isinstance(inst, dict):
            state = (inst.get("state") or inst.get("status") or "").lower()
            if state in ("open", "connected"):
                out["hint"] = "✅ Sesión ya activa en Evolution. Para un QR nuevo: «Cerrar sesión» abajo."
                log_event(
                    "evolution_qr",
                    {"outcome": "session_open_no_qr", "connect_http": http, "state": state},
                )
                return out
            if state == "connecting":
                out["hint"] = "Conectando… Si no aparece QR en unos segundos, recargá o cerrá sesión abajo."
                log_event(
                    "evolution_qr",
                    {"outcome": "connecting_no_image", "connect_http": http},
                )
                return out

        out["hint"] = "Sin QR todavía: " + json.dumps(data, ensure_ascii=False)[:220]
        log_event(
            "evolution_qr",
            {
                "outcome": "no_qr_snippet",
                "connect_http": http,
                "keys": list(data.keys())[:30],
            },
        )
        return out
    except Exception as e:
        out["error"] = str(e)[:200]
        log_event("evolution_qr", {"outcome": "exception", "error": str(e)[:300]})
        return out


def html_qr_page(*, api_prefix: str) -> str:
    """
    Página HTML. ``api_prefix`` debe ser la ruta base de la API en el mismo origen,
    p. ej. ``/evolution/api`` (sin barra final).
    """
    ap = api_prefix.rstrip("/")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis — QR WhatsApp (Evolution)</title>
<style>
  :root {{ --wa:#25d366; --bg:#0f0f12; --card:#1a1a20; --muted:#9aa0a6; }}
  body {{ font-family: system-ui, sans-serif; background: var(--bg); color: #eee;
          margin: 0; min-height: 100vh; display: flex; flex-direction: column; align-items: center;
          padding: 24px 16px 48px; box-sizing: border-box; }}
  h1 {{ color: var(--wa); font-size: 1.35rem; margin: 0 0 8px; text-align: center; }}
  .sub {{ color: var(--muted); font-size: 14px; text-align: center; max-width: 420px; margin-bottom: 20px; }}
  .card {{ background: var(--card); border-radius: 16px; padding: 20px; max-width: 400px; width: 100%;
           box-sizing: border-box; }}
  #qrbox {{ min-height: 300px; display: grid; place-items: center; }}
  #qrbox img {{ max-width: 100%; height: auto; border-radius: 12px; background: #fff; padding: 10px; }}
  .state {{ font-size: 13px; color: var(--muted); margin-top: 12px; text-align: center; }}
  .err {{ color: #ff7b72; font-size: 14px; }}
  .ok {{ color: var(--wa); font-size: 14px; }}
  label {{ display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }}
  input[type=password] {{ width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #333;
                          background: #111; color: #eee; box-sizing: border-box; }}
  button {{ width: 100%; margin-top: 10px; padding: 12px; border: none; border-radius: 10px;
            font-size: 15px; cursor: pointer; font-weight: 600; }}
  .btn-out {{ background: #c44; color: #fff; }}
  .btn-out:hover {{ filter: brightness(1.08); }}
  .steps {{ margin-top: 24px; font-size: 14px; color: var(--muted); line-height: 1.5; }}
  .steps ol {{ padding-left: 20px; }}
  .hint {{ font-size: 12px; color: #666; margin-top: 8px; }}
</style>
</head>
<body>
  <h1>📱 Jarvis — Vincular WhatsApp</h1>
  <p class="sub">Instancia Evolution: <strong>{INSTANCE}</strong> · El QR se renueva solo cada 2 segundos (WhatsApp lo invalida rápido).</p>
  <div class="card">
    <div id="qrbox"><p class="muted">Cargando QR…</p></div>
    <p id="status" class="state"></p>
    <p id="pair" class="state"></p>
  </div>
  <div class="card" style="margin-top:16px">
    <label for="tok">Para <strong>cerrar sesión</strong> y generar un QR nuevo (sincronizar el celular):</label>
    <input id="tok" type="password" autocomplete="off" placeholder="Misma clave que AGENT_SECRET / login Jarvis" />
    <button type="button" class="btn-out" id="btnLogout">Cerrar sesión en Evolution y pedir QR nuevo</button>
    <p id="logoutMsg" class="state"></p>
    <p class="hint">Tras desvincular, escaneá el QR con WhatsApp → Dispositivos vinculados. Así se renuevan claves y suele desaparecer “Esperando mensaje” en el móvil.</p>
  </div>
  <div class="steps">
    <ol>
      <li>WhatsApp en el celular → ⋮ → <b>Dispositivos vinculados</b> → <b>Vincular un dispositivo</b></li>
      <li>Apuntá la cámara al código (o usá emparejamiento por código si Evolution lo muestra)</li>
      <li>Esperá a que diga “Sesión activa” arriba</li>
    </ol>
  </div>
<script>
const API = {json.dumps(ap)};
async function refreshQr() {{
  const st = document.getElementById('status');
  const pair = document.getElementById('pair');
  const box = document.getElementById('qrbox');
  try {{
    const r = await fetch(API + '/qr', {{ cache: 'no-store' }});
    const j = await r.json();
    if (j.base64) {{
      box.innerHTML = '<img src="' + j.base64.replace(/"/g, '&quot;') + '" alt="QR WhatsApp" />';
      st.textContent = j.hint || 'Escaneá el código con WhatsApp.';
      st.className = 'state ok';
    }} else {{
      box.innerHTML = '<p class="ok">' + (j.hint || 'Sin QR (¿ya vinculado?)') + '</p>';
      st.textContent = '';
    }}
    pair.textContent = j.pairingCode ? ('Código: ' + j.pairingCode) : '';
    if (j.error) {{ st.innerHTML = '<span class="err">' + j.error + '</span>'; st.className = 'state'; }}
  }} catch (e) {{
    box.innerHTML = '<p class="err">Error de red al pedir QR</p>';
  }}
}}
document.getElementById('btnLogout').onclick = async () => {{
  const tok = document.getElementById('tok').value.trim();
  const msg = document.getElementById('logoutMsg');
  msg.textContent = 'Procesando…';
  try {{
    const r = await fetch(API + '/logout', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ token: tok }})
    }});
    const j = await r.json();
    if (j.ok) {{
      msg.textContent = 'Sesión cerrada. En unos segundos debería aparecer el QR nuevo.';
      msg.className = 'state ok';
      setTimeout(refreshQr, 800);
    }} else {{
      msg.textContent = j.detail || 'No se pudo cerrar sesión';
      msg.className = 'state err';
    }}
  }} catch (e) {{
    msg.textContent = String(e);
    msg.className = 'state err';
  }}
}};
setInterval(refreshQr, 2000);
refreshQr();
</script>
</body>
</html>
"""


def _header_get(headers: Mapping[str, str], name: str) -> str:
    nl = name.lower()
    for k, v in headers.items():
        if str(k).lower() == nl:
            return str(v or "").strip()
    return ""


def collect_logout_token(raw_bytes: bytes, headers: Mapping[str, str] | None) -> tuple[str, bool, str]:
    """
    Devuelve (token, json_invalido, instance_override).
    ``instance`` / ``instanceName`` en JSON: instancia Evolution a desconectar (opcional).
    """
    h = headers or {}
    token = ""
    instance_override = ""
    json_ok = True
    try:
        body = json.loads(raw_bytes.decode("utf-8") if raw_bytes else "{}")
        if isinstance(body, dict):
            token = (body.get("token") or "").strip()
            instance_override = (
                body.get("instance") or body.get("instanceName") or ""
            ).strip()
    except Exception:
        json_ok = False
    if not token:
        auth = _header_get(h, "authorization")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        token = _header_get(h, "x-agent-secret")
    token = token.strip()
    json_invalid = not json_ok and not token
    return token, json_invalid, instance_override


def _logout_with_token(token: str, evolution_instance: str | None = None) -> tuple[int, dict]:
    if not (AGENT_SECRET or "").strip():
        log_event("logout_bridge", {"step": "agent_secret_missing"})
        return 503, {
            "ok": False,
            "detail": "Falta AGENT_SECRET en el .env del servidor; definilo y reiniciá el bridge.",
            "code": "agent_secret_missing",
        }
    if token != AGENT_SECRET:
        log_event(
            "logout_bridge",
            {
                "step": "token_mismatch",
                "sent_len": len(token),
                "expected_secret_len": len(AGENT_SECRET),
            },
        )
        return 403, {"ok": False, "detail": "Token incorrecto (usá la misma clave que AGENT_SECRET en .env)"}

    target_inst = (evolution_instance or INSTANCE or "").strip()
    try:
        result = do_logout(evolution_instance)
        http = result.get("http", 0)
        if 200 <= http < 300:
            log_event(
                "logout_bridge",
                {"step": "evolution_logout_ok", "evo_http": http, "instance": target_inst},
            )
            return 200, {
                "ok": True,
                "evolution": result.get("data"),
                "evolution_logout_applied": True,
            }
        hint = _evolution_error_hint(http, result)
        log_event(
            "logout_bridge",
            {
                "step": "evolution_logout_fail",
                "evo_http": http,
                "instance": target_inst,
                "detail": hint[:500],
            },
        )
        return 502, {
            "ok": False,
            "detail": hint,
            "raw": result,
            "evolution_logout_applied": False,
        }
    except Exception as e:
        log_event(
            "logout_bridge",
            {"step": "exception", "error": str(e)[:400], "error_type": type(e).__name__},
        )
        return 500, {"ok": False, "detail": str(e)[:200]}


def handle_logout_post_body(
    raw_bytes: bytes, headers: Mapping[str, str] | None = None
) -> tuple[int, dict]:
    """
    Valida token y llama Evolution. Devuelve (status_http, dict JSON).

    ``headers`` (opcional): cabeceras HTTP del request; si el proxy rompe el JSON,
    el token puede ir en ``Authorization: Bearer`` o ``X-Agent-Secret``.
    """
    h = headers or {}
    auth = _header_get(h, "authorization")
    token, json_bad, inst_over = collect_logout_token(raw_bytes, headers)
    log_event(
        "logout_bridge",
        {
            "step": "parse_request",
            "json_invalid": json_bad,
            "has_token": bool(token),
            "token_len": len(token) if token else 0,
            "instance_override": bool(inst_over),
            "has_x_agent_secret_header": bool(_header_get(h, "x-agent-secret")),
            "has_authorization_bearer": auth.lower().startswith("bearer ") if auth else False,
            "body_bytes": len(raw_bytes or b""),
        },
    )
    if json_bad:
        return 400, {"ok": False, "detail": "JSON inválido"}
    if not token:
        return 400, {
            "ok": False,
            "detail": "Falta token (JSON body.token, Authorization: Bearer o cabecera X-Agent-Secret)",
        }
    inst = inst_over.strip() if inst_over else None
    return _logout_with_token(token, inst)
