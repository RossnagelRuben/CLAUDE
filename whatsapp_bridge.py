"""
Jarvis WhatsApp Bridge — integración con Evolution API.

Recibe mensajes de WhatsApp, los procesa con Claude (mismo system prompt que Telegram)
y responde automáticamente. Replica la lógica de jarvis_bot.py para WhatsApp.

Iniciar:
    ./venv/bin/uvicorn whatsapp_bridge:app --host 0.0.0.0 --port 8766

Flujo:
    WhatsApp → Evolution API webhook → POST /webhook → Claude → Evolution API → WhatsApp

Seguridad:
    - Solo responde a WHATSAPP_ALLOWED_NUMBERS (definidos en .env)
    - Requiere autenticación con AGENT_SECRET antes de responder
    - Sesiones con expiración configurable (WHATSAPP_SESSION_HOURS)
"""

import asyncio
import base64
import html
import json
import time
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from evolution_qr import (
    ADMIN_EVOLUTION_QR_API_PREFIX,
    API_PREFIX,
    build_api_qr_response,
    evolution_instances_for_panel,
    handle_logout_post_body,
    html_qr_page,
)
from server_metrics import append_sample_if_due, get_dashboard_payload

from whatsapp_debug_log import (
    LOG_FILE,
    debug_log_enabled,
    get_recent_debug_events,
    log_event,
    log_http_request,
)

from drr.chat_intents import (
    parse_edit_image_intent,
    parse_followup_last_image_edit_intent,
    parse_producto_imagen_index,
    parse_save_image_to_drr_product_index,
    parse_upload_whatsapp_catalog_index,
    user_requested_ai_image_generation,
)

try:
    import anthropic  # opcional: solo si CLAUDE_API_KEY está configurado
except ImportError:
    anthropic = None

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# =========================
# CONFIG
# =========================
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
AGENT_SECRET = os.getenv("AGENT_SECRET", "").strip()
ADMIN_PANEL_TOKEN = os.getenv("ADMIN_PANEL_TOKEN", "").strip() or AGENT_SECRET
TRANSCRIBE_API_URL = os.getenv("TRANSCRIBE_API_URL", "").strip()
# Misma clave que Telegram para DALL·E (fallback si Gemini Imagen falla).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# Si es True, WhatsApp intenta primero faster_whisper local y luego N8N (orden inverso al default).
WHATSAPP_TRANSCRIBE_LOCAL_FIRST = os.getenv("WHATSAPP_TRANSCRIBE_LOCAL_FIRST", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "si",
    "sí",
)
PROMPT_FILE = BASE_DIR / "agent_prompt.txt"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)
# Medios y archivos guardados desde WhatsApp (panel /admin/inbox)
WA_INBOX_ROOT = BASE_DIR / "wa_inbox"
WA_INBOX_MEDIA = WA_INBOX_ROOT / "media"

# Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").strip()   # ej: http://localhost:8080
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "").strip()   # apikey de Evolution
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "jarvis").strip()
# URL publica de la pagina QR (opcional). Si esta vacio, el panel usa mismo origen + /evolution/
# (servido por este bridge; ya no hace falta qr_server en 8099 salvo que quieras otro puerto).
QR_WEB_PUBLIC_URL = os.getenv("QR_WEB_PUBLIC_URL", "").strip()

# Números autorizados: formato 549XXXXXXXXXX (sin @s.whatsapp.net, sin +)
# Separados por coma. Si está vacío, solo bloquea — SIEMPRE definir esto.
#
# Importante: usamos lista ordenada (desde .env) para definir el "dueño" de forma
# determinística. NO podemos usar un `set` porque cambia el iterador.
_allowed_numbers_env_raw = [
    re.sub(r"\D", "", n.strip())
    for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",")
    if n.strip() and re.sub(r"\D", "", n.strip())
]
WHATSAPP_ALLOWED_ORDERED = []
for d in _allowed_numbers_env_raw:
    if d and d not in WHATSAPP_ALLOWED_ORDERED:
        WHATSAPP_ALLOWED_ORDERED.append(d)
WHATSAPP_ALLOWED = set(WHATSAPP_ALLOWED_ORDERED)
WHATSAPP_SELF_LID = os.getenv("WHATSAPP_SELF_LID", "").strip()

# Número principal para contestar cuando `fromMe=True` (mensajes del dueño).
_owner_phone_env = re.sub(r"\D", "", os.getenv("WHATSAPP_OWNER_PHONE", "").strip())
WHATSAPP_PRIMARY_OWNER_PHONE = (
    _owner_phone_env
    if _owner_phone_env
    else (WHATSAPP_ALLOWED_ORDERED[0] if WHATSAPP_ALLOWED_ORDERED else "owner")
)

# Algunos webhooks de Evolution entregan un `remoteJid`/`phone_norm` que no coincide
# 1:1 con el número que Evolution acepta al momento de enviar (`exists: false`).
# Esto permite mapear `phone_norm` (entrada) -> número real (salida) para enviar.
# Formato en .env:  "norm1:real1,norm2:real2"
_phone_map_raw = os.getenv("WHATSAPP_PHONE_MAP", "").strip()
WHATSAPP_PHONE_MAP: dict[str, str] = {}
if _phone_map_raw:
    for pair in _phone_map_raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            logger.warning("WHATSAPP_PHONE_MAP inválido (faltó ':'): %r", pair)
            continue
        src, dst = pair.split(":", 1)
        src_norm = re.sub(r"\D", "", src.strip())
        dst_norm = re.sub(r"\D", "", dst.strip())
        if src_norm and dst_norm:
            WHATSAPP_PHONE_MAP[src_norm] = dst_norm


def _refresh_whatsapp_config_from_env() -> None:
    """
    Relee .env y actualiza WHATSAPP_ALLOWED / WHATSAPP_PHONE_MAP en memoria.
    Usado por el panel admin para aplicar cambios sin reiniciar.
    """
    global WHATSAPP_ALLOWED, WHATSAPP_PHONE_MAP, WHATSAPP_PRIMARY_OWNER_PHONE

    # override=True para que tome valores nuevos del .env
    load_dotenv(BASE_DIR / ".env", override=True)

    WHATSAPP_ALLOWED = {
        re.sub(r"\D", "", n.strip())
        for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",")
        if n.strip() and re.sub(r"\D", "", n.strip())
    }

    _phone_map_raw = os.getenv("WHATSAPP_PHONE_MAP", "").strip()
    phone_map: dict[str, str] = {}
    if _phone_map_raw:
        for pair in _phone_map_raw.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if ":" not in pair:
                logger.warning("WHATSAPP_PHONE_MAP invalido (faltó ':'): %r", pair)
                continue
            src, dst = pair.split(":", 1)
            src_norm = re.sub(r"\D", "", src.strip())
            dst_norm = re.sub(r"\D", "", dst.strip())
            if src_norm and dst_norm:
                phone_map[src_norm] = dst_norm
    WHATSAPP_PHONE_MAP = phone_map

    owner_phone_env = re.sub(r"\D", "", os.getenv("WHATSAPP_OWNER_PHONE", "").strip())
    if owner_phone_env:
        WHATSAPP_PRIMARY_OWNER_PHONE = owner_phone_env
    else:
        allowed_ordered = [
            re.sub(r"\D", "", n.strip())
            for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",")
            if n.strip() and re.sub(r"\D", "", n.strip())
        ]
        WHATSAPP_PRIMARY_OWNER_PHONE = allowed_ordered[0] if allowed_ordered else "owner"

# DRR
DRR_API_BASE_URL = os.getenv("DRR_API_BASE_URL", "").strip()
DRR_API_KEY = os.getenv("DRR_API_KEY", "").strip()

SESSION_HOURS = int(os.getenv("WHATSAPP_SESSION_HOURS", "8"))

# Backend de texto: preferimos Gemini si está configurado y dejamos Claude como fallback.
_has_gemini_text = bool(GEMINI_API_KEY)
if not CLAUDE_API_KEY and not _has_gemini_text:
    raise RuntimeError("Falta al menos un backend de IA de texto: GEMINI_API_KEY o CLAUDE_API_KEY en .env")
if not AGENT_SECRET:
    raise RuntimeError("Falta AGENT_SECRET en .env")

claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY) if (CLAUDE_API_KEY and anthropic) else None
BACKEND_NAME = "Gemini" if _has_gemini_text else "Claude"
app = FastAPI(title="Jarvis WhatsApp Bridge", version="1.0")


@app.middleware("http")
async def jarvis_whatsapp_http_debug(request: Request, call_next):
    """Registra cada request HTTP en ``logs/whatsapp_debug.log`` (ver WHATSAPP_DEBUG_LOG)."""
    start = time.perf_counter()
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    client = request.client.host if request.client else "?"
    xf = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xf:
        client = xf.split(",", 1)[0].strip()
    response = await call_next(request)
    try:
        log_http_request(
            method=request.method,
            path=path,
            client_host=client,
            status_code=response.status_code,
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception:
        pass
    return response


@app.get("/j/ping")
def j_bridge_ping() -> dict:
    """Comprobación mínima: si esto da 404, este puerto no es el bridge actual."""
    return {
        "ok": True,
        "service": "jarvis-whatsapp-bridge",
        "evolution_instance_env": EVOLUTION_INSTANCE,
        "evolution_api_configured": bool(EVOLUTION_API_URL and EVOLUTION_API_KEY),
    }


@app.post("/j/logout")
async def j_logout_post(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


@app.get("/j/logout")
async def j_logout_get(
    request: Request,
    token: str = Query(default=""),
    instance: str = Query(default=""),
) -> JSONResponse:
    body: dict = {"token": token}
    if instance.strip():
        body["instance"] = instance.strip()
    raw = json.dumps(body).encode("utf-8")
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


@app.post("/j/instances")
async def j_evolution_instances(request: Request) -> JSONResponse:
    """Lista instancias Evolution (misma clave AGENT_SECRET). Rutas cortas por si /admin/* no llega."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("token") or "").strip()
    if not AGENT_SECRET:
        return JSONResponse({"ok": False, "detail": "AGENT_SECRET no configurado"}, status_code=503)
    if token != AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "Clave incorrecta (AGENT_SECRET)"},
            status_code=403,
        )
    out = evolution_instances_for_panel()
    return JSONResponse(content=out, status_code=200 if out.get("ok") else 502)


@app.post("/j/debug-events")
async def j_debug_events(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("token") or "").strip()
    try:
        limit = int(body.get("limit") or 80)
    except (TypeError, ValueError):
        limit = 80
    if not AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "AGENT_SECRET no configurado en el servidor"},
            status_code=503,
        )
    if token != AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "Clave incorrecta (usá el mismo valor que AGENT_SECRET)"},
            status_code=403,
        )
    events = get_recent_debug_events(limit)
    return JSONResponse({"ok": True, "events": events, "count": len(events)})


@app.get("/evolution")
def evolution_qr_redirect() -> RedirectResponse:
    return RedirectResponse(url="/evolution/", status_code=307)


@app.get("/evolution/", response_class=HTMLResponse)
def evolution_qr_page_view() -> HTMLResponse:
    """
    Misma UI que ``qr_server.py``: QR de Evolution + cerrar sesión, sin depender del puerto 8099.
    Abrí: ``http://TU_IP:8766/evolution/``
    """
    return HTMLResponse(content=html_qr_page(api_prefix=API_PREFIX))


@app.get("/evolution/api/qr")
def evolution_qr_api_json() -> dict:
    return build_api_qr_response()


@app.post("/evolution/api/logout")
async def evolution_qr_api_logout(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


@app.get("/evolution/api/logout")
async def evolution_qr_api_logout_get(request: Request, token: str = Query(default="")) -> JSONResponse:
    """Mismo efecto que POST; util si el panel no puede hacer POST (proxy)."""
    raw = json.dumps({"token": token}).encode("utf-8")
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


# Rutas muy cortas (ultimo recurso si /admin/... o /evolution/... no llegan al bridge por nginx).
@app.post("/walogout")
async def wa_logout_post(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


@app.get("/walogout")
async def wa_logout_get(request: Request, token: str = Query(default="")) -> JSONResponse:
    raw = json.dumps({"token": token}).encode("utf-8")
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


# --- Misma página QR bajo /admin/whatsapp/... (mismo host que el panel; evita 404 si /evolution/ no llega al bridge) ---
@app.get("/admin/whatsapp/evolution")
def admin_whatsapp_evolution_qr_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/whatsapp/evolution/", status_code=307)


@app.get("/admin/whatsapp/evolution/", response_class=HTMLResponse)
def admin_whatsapp_evolution_qr_page() -> HTMLResponse:
    """
    Duplicado de ``/evolution/``: abrí ``http://TU_IP:8766/admin/whatsapp/evolution/``
    """
    return HTMLResponse(content=html_qr_page(api_prefix=ADMIN_EVOLUTION_QR_API_PREFIX))


@app.get("/admin/whatsapp/evolution/api/qr")
def admin_whatsapp_evolution_qr_api_json() -> dict:
    return build_api_qr_response()


@app.post("/admin/whatsapp/evolution/api/logout")
async def admin_whatsapp_evolution_qr_api_logout(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


# =========================
# ESTADO EN MEMORIA
# =========================
# Sesiones activas: phone → datetime de expiración
_sessions: dict[str, datetime] = {}
# Historial de conversación: phone → lista de mensajes (últimas MAX_TURNS rondas)
_history: dict[str, list[dict]] = {}
# Comandos pendientes de confirmación: phone → comando
_pending_cmd: dict[str, str] = {}
MAX_TURNS = 6  # últimas 6 rondas (12 mensajes)
# Textos recién enviados por Jarvis — para no procesar el eco del fromMe propio.
# Guardamos también el jid para evitar falsos positivos entre chats distintos.
_sent_texts: list[tuple[str, str, datetime]] = []
_voice_busy: set[str] = set()
# Última imagen por número (p. ej. tras BUSCAR_IMAGEN), para futuras ediciones tipo Telegram.
_last_image_wa: dict[str, dict] = {}


def _wa_phone_aliases(phone: str) -> set[str]:
    """Variantes de dígitos del mismo usuario (p. ej. 11 vs 54… según WHATSAPP_PHONE_MAP)."""
    p = re.sub(r"\D", "", phone or "")
    if not p:
        return set()
    keys = {p}
    mapped = WHATSAPP_PHONE_MAP.get(p)
    if mapped:
        keys.add(mapped)
    for k, v in WHATSAPP_PHONE_MAP.items():
        if v == p:
            keys.add(k)
    return keys


def _last_image_wa_put(phone: str, payload: dict) -> None:
    b = payload.get("bytes")
    mt = (payload.get("mime_type") or "image/jpeg").strip() or "image/jpeg"
    if not b:
        return
    for k in _wa_phone_aliases(phone):
        _last_image_wa[k] = {"bytes": b, "mime_type": mt}


def _last_image_wa_pick(phone: str) -> dict | None:
    for k in _wa_phone_aliases(phone):
        data = _last_image_wa.get(k)
        if data and data.get("bytes"):
            return data
    return None
# Último listado DRR mostrado (mismo orden que el texto: índice 1 = primer producto).
_last_drr_products: dict[str, list[dict]] = {}


def _last_drr_products_put(phone: str, snapshots: list[dict]) -> None:
    """Replica el listado en todas las variantes de número (WHATSAPP_PHONE_MAP)."""
    lst = list(snapshots) if snapshots else []
    for k in _wa_phone_aliases(phone):
        _last_drr_products[k] = lst
    # Nuevo listado: el contexto de «última foto de catálogo» ya no coincide con los índices.
    _last_drr_imagen_context_put(phone, None)


# Último producto DRR del que se mostró imagen (para PATCH imagen tras edición).
_last_drr_imagen_context: dict[str, dict] = {}


def _last_drr_imagen_context_put(phone: str, ctx: dict | None) -> None:
    for k in _wa_phone_aliases(phone):
        if ctx:
            _last_drr_imagen_context[k] = dict(ctx)
        else:
            _last_drr_imagen_context.pop(k, None)


def _last_drr_imagen_context_pick(phone: str) -> dict | None:
    for k in _wa_phone_aliases(phone):
        c = _last_drr_imagen_context.get(k)
        if c and c.get("codigo_id"):
            return c
    return None


def _last_drr_products_pick(phone: str) -> list[dict]:
    for k in _wa_phone_aliases(phone):
        lst = _last_drr_products.get(k) or []
        if lst:
            return lst
    return []


def _session_ok(phone: str) -> bool:
    exp = _sessions.get(phone)
    return exp is not None and datetime.now() < exp


def _open_session(phone: str) -> None:
    _sessions[phone] = datetime.now() + timedelta(hours=SESSION_HOURS)
    logger.info("Sesión WhatsApp abierta para %s (expira en %dh)", phone, SESSION_HOURS)


def _system_prompt() -> str:
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text(encoding="utf-8")
    return (
        "Sos Jarvis, asistente de servidor. Respondé siempre en español, breve y claro. "
        "Podés proponer comandos con ACCION: o CMD: (el usuario confirmará). "
        "Guardá notas con NOTA: y realizá búsquedas con BUSCAR:. "
        "Si piden ver o listar notas guardadas, respondé solo con la línea LISTAR_NOTAS: "
        "(nunca uses CMD: ni cat/ls sobre rutas inventadas como ~/.jarvis_notes.txt). "
        "Respetá estrictamente el formato de acciones ya definido para Jarvis."
    )


def _ask_claude(text: str, phone: str) -> str:
    """Llama a Claude con historial de conversación del número."""
    if not claude:
        return "(Claude no disponible. Configurá CLAUDE_API_KEY en .env o usá Gemini.)"
    history = _history.get(phone, [])
    history.append({"role": "user", "content": text})

    response = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=_system_prompt(),
        messages=history[-(MAX_TURNS * 2):],
    )
    reply = response.content[0].text.strip() if response.content else "(sin respuesta)"
    history.append({"role": "assistant", "content": reply})
    _history[phone] = history[-(MAX_TURNS * 2):]
    return reply


def _ask_gemini(text: str, phone: str) -> str:
    """
    Llama a Gemini con historial de conversación del número.
    Mantiene el mismo comportamiento que Claude (historia corta) pero usando GEMINI_API_KEY.
    """
    if not GEMINI_API_KEY:
        return "(Gemini no configurado. Configurá GEMINI_API_KEY en .env.)"
    try:
        from google import genai
    except ImportError as e:
        return f"(Gemini no disponible, falta dependencia google-genai: {e})"

    history = _history.get(phone, [])
    history.append({"role": "user", "content": text})

    def _build_contents(msgs: list[dict], *, include_roles: bool = True) -> list[dict]:
        """
        Construye el payload para Gemini.
        Gemini espera roles permitidos (user/model); evitamos otros para que no rompa con 400.
        """
        payload = []
        for msg in msgs:
            content = msg.get("content", "")
            if not include_roles:
                payload.append({"role": "user", "parts": [{"text": str(content)}]})
                continue

            role = msg.get("role") or "user"
            if role == "assistant":
                role = "model"
            if role not in ("user", "model"):
                role = "user"
            payload.append({"role": role, "parts": [{"text": str(content)}]})
        return payload

    # 1) Intentamos con historial (útil para mantener contexto).
    contents_full = _build_contents(history[-(MAX_TURNS * 2):])
    # 2) Fallback: si el payload con historial falla (INVALID_ARGUMENT), intentamos solo el último mensaje.
    contents_last_user = _build_contents([{"role": "user", "content": text}])

    client_gemini = genai.Client(api_key=GEMINI_API_KEY)
    # Nota: algunos modelos históricos pueden devolver 404 para nuevas cuentas.
    # Por eso: intentamos el modelo configurado y, si falla, probamos alternativas.
    primary = os.getenv("GEMINI_TEXT_MODEL", "").strip() or "gemini-1.5-flash"
    fallbacks = [
        primary,
        "gemini-1.5-flash-002",
        "gemini-1.5-pro",
        "gemini-2.5-flash",
    ]

    last_error = None
    for contents_to_try in (contents_full, contents_last_user):
        for model_name in fallbacks:
            try:
                response = client_gemini.models.generate_content(
                    model=model_name,
                    contents=contents_to_try,
                    config={"system_instruction": _system_prompt()},
                )
                reply = (response.text or "").strip()
                if not reply:
                    reply = "(sin respuesta)"
                history.append({"role": "assistant", "content": reply})
                _history[phone] = history[-(MAX_TURNS * 2):]
                return reply
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                # Intentamos todos los modelos; 404/NotFound suele ser normal para cuentas nuevas.
                if "404" in msg or "not_found" in msg or "no longer available" in msg:
                    continue
                # Para INVALID_ARGUMENT u otros, probamos el resto de modelos y finalmente sin historial.
                continue

    err = str(last_error) if last_error else "error desconocido"
    err = err.replace("\n", " ").replace("\r", " ")
    err = err[:500]
    reply = f"(Error Gemini: {err})"
    history.append({"role": "assistant", "content": reply})
    _history[phone] = history[-(MAX_TURNS * 2):]
    return reply


def _gemini_transcribe_audio(audio_path: str) -> str | None:
    """
    Fallback cuando transcribe_core falla o no puede decodificar el audio.
    Sube el audio a Gemini (Files API) y pide SOLO transcripción en español.
    """
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
    except ImportError as e:
        logger.warning("Gemini no disponible para transcribir audio: %s", e)
        return None

    try:
        try:
            from pathlib import Path as _Path
            sz = _Path(audio_path).stat().st_size
        except Exception:
            sz = -1
        logger.info("Gemini audio fallback: subiendo audio_path=%r bytes=%s", audio_path, sz)
        client = genai.Client(api_key=GEMINI_API_KEY)
        uploaded = client.files.upload(file=audio_path)
        logger.info("Gemini audio fallback: upload OK (uri=%r)", getattr(uploaded, "uri", None))
    except Exception as e:
        logger.warning("Error subiendo audio a Gemini (path=%r): %s", audio_path, e)
        return None

    primary = os.getenv("GEMINI_TEXT_MODEL", "").strip() or "gemini-2.5-flash"
    fallbacks = [
        primary,
        "gemini-2.5-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-002",
    ]

    prompt = (
        "Transcribí el audio a texto en español. "
        "Respondé SOLO con la transcripción (sin encabezados, sin ACCION:/CMD:)."
    )

    last_error: Exception | None = None
    for model_name in fallbacks:
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=[prompt, uploaded],
                config={"system_instruction": "Eres un asistente de transcripción."},
            )
            txt = (resp.text or "").strip()
            if txt:
                logger.info("Gemini audio fallback: transcripción OK model=%r head=%r", model_name, txt[:80])
                return txt
        except Exception as e:
            last_error = e
            continue

    if last_error:
        logger.warning("Gemini falló transcribiendo audio: %s", str(last_error)[:250])
    return None


def _extract_base64_any(obj: object) -> str | None:
    """
    Extrae el primer string que parezca base64 desde un objeto JSON.
    Usado porque Evolution puede devolver distintas estructuras según el caso.
    """
    b64_re = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")

    def _walk(v: object) -> str | None:
        if isinstance(v, str):
            s = v.strip()
            # Evitar falsos positivos (base64 largo para medias).
            if len(s) >= 100 and b64_re.match(s):
                return s
            return None
        if isinstance(v, dict):
            for vv in v.values():
                hit = _walk(vv)
                if hit:
                    return hit
            return None
        if isinstance(v, list):
            for vv in v:
                hit = _walk(vv)
                if hit:
                    return hit
        return None

    return _walk(obj)


def _evolution_download_media_base64(
    media_key_id: str, *, phone: str, min_bytes: int = 1024
) -> bytes | None:
    """
    Descarga media decodificable desde Evolution usando getBase64FromMediaMessage.
    Devuelve bytes (audio) o None si falla.
    """
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        return None
    if not media_key_id:
        return None

    url = f"{EVOLUTION_API_URL.rstrip('/')}/chat/getBase64FromMediaMessage/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY}
    body = {"message": {"key": {"id": media_key_id}}, "convertToMp4": False}

    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0, read=120.0)) as client:
            resp = client.post(url, json=body, headers=headers)

        logger.info("[%s] Evolution getBase64FromMediaMessage status=%s", phone, resp.status_code)
        if resp.status_code < 200 or resp.status_code >= 300:
            logger.warning("[%s] Evolution getBase64FromMediaMessage body_head=%r", phone, (resp.text or "")[:200])
            return None

        content_type = (resp.headers.get("content-type") or "").lower()
        base64_str: str | None = None
        if "application/json" in content_type:
            try:
                js = resp.json()
                base64_str = (
                    js.get("base64")
                    or (js.get("response") or {}).get("base64")
                    or (js.get("data") or {}).get("base64")
                    or _extract_base64_any(js)
                )
            except Exception:
                base64_str = None

        if not base64_str:
            base64_str = (resp.text or "").strip()

        if not base64_str:
            return None

        audio_bytes = base64.b64decode(base64_str, validate=False)
        if len(audio_bytes) < min_bytes:
            logger.warning(
                "[%s] Media (base64) demasiado chica: %s bytes (min=%s)",
                phone,
                len(audio_bytes),
                min_bytes,
            )
            return None
        return audio_bytes
    except Exception as e:
        logger.exception("[%s] Error descargando audio por base64 desde Evolution: %s", phone, e)
        return None


def _transcribe_audio_via_api(audio_path: str, phone: str) -> str | None:
    """
    Si TRANSCRIBE_API_URL está configurado, envía el audio por HTTP multipart.
    Espera respuesta tipo: {"text": "..."}.
    """
    if not TRANSCRIBE_API_URL:
        return None

    try:
        url = TRANSCRIBE_API_URL.rstrip("/")
        headers = {}
        # Para que coincida con el workflow de N8N / API, el campo se llama "audio".
        with open(audio_path, "rb") as f:
            filename = Path(audio_path).name or "voice.ogg"
            files = {"audio": (filename, f, "audio/ogg")}
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=20.0, read=180.0)) as client:
                resp = client.post(url, files=files, headers=headers)

        if resp.status_code != 200:
            logger.warning("[%s] Transcribe API no OK status=%s body_head=%r", phone, resp.status_code, (resp.text or "")[:200])
            return None

        try:
            js = resp.json()
        except Exception:
            logger.warning("[%s] Transcribe API no devolvió JSON", phone)
            return None

        if isinstance(js, dict):
            txt = js.get("text")
            if isinstance(txt, str) and txt.strip():
                logger.info("[%s] Transcribe API OK head=%r", phone, txt.strip()[:80])
                return txt.strip()
            # Compatibilidad con algunas respuestas tipo { body: { text: ... } }
            body = js.get("body")
            if isinstance(body, dict):
                txt2 = body.get("text")
                if isinstance(txt2, str) and txt2.strip():
                    logger.info("[%s] Transcribe API OK (body) head=%r", phone, txt2.strip()[:80])
                    return txt2.strip()

        return None
    except Exception as e:
        logger.exception("[%s] Error llamando Transcribe API: %s", phone, e)
        return None


def _ffmpeg_convert_to_wav(input_path: str, output_path: str, phone: str) -> bool:
    """
    Convierte audio a WAV PCM 16k/mono para maximizar decodificación.
    Devuelve True si el archivo de salida existe y tiene tamaño razonable.
    """
    if not shutil.which("ffmpeg"):
        return False
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            input_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p = Path(output_path)
        if p.exists() and p.stat().st_size >= 1024:
            return True
    except Exception as e:
        logger.warning("[%s] ffmpeg convert a wav falló: %s", phone, e)
    return False


def _looks_like_valid_transcription(txt: str) -> bool:
    """
    Heurística simple para distinguir transcripción real vs. respuestas genéricas/error.
    """
    if not txt:
        return False
    t = txt.strip()
    if not t or t.lower() == "(sin voz detectada)":
        return False
    # Si Gemini devuelve mensajes tipo "No puedo transcribir..." no son transcripción.
    bad_markers = [
        "soy un modelo de lenguaje",
        "no puedo transcribir",
        "no me lo has proporcionado",
        "entendido. soy jarvis",
        "error gemini",
    ]
    tl = t.lower()
    for m in bad_markers:
        if m in tl:
            return False
    # Debe tener al menos una letra.
    if not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", t):
        return False
    # Evitar strings demasiado cortos.
    if len(t) < 3:
        return False
    return True


def _transcribe_whatsapp_audio(effective_path: str, phone: str, *, used_base64: bool) -> str | None:
    """
    Misma cadena que Telegram: N8N/multipart → `transcription_api` → `faster_whisper` en `transcribe_core`.
    Si `WHATSAPP_TRANSCRIBE_LOCAL_FIRST=1`, se invierte el orden (útil si el webhook N8N falla o tarda).
    Gemini solo como último recurso cuando `used_base64` (audio decodificado por Evolution).
    """
    import transcribe_core

    def _local() -> str | None:
        try:
            t = transcribe_core.transcribe_voice(effective_path)
            if t and t.strip() and t.strip() != "(sin voz detectada)":
                return t.strip()
        except Exception as e:
            logger.exception("[%s] transcribe_core (faster_whisper) falló: %s", phone, e)
        return None

    def _api() -> str | None:
        t = _transcribe_audio_via_api(effective_path, phone)
        if t and t.strip() and t.strip() != "(sin voz detectada)":
            return t.strip()
        return None

    def _gemini() -> str | None:
        if not used_base64:
            return None
        g = _gemini_transcribe_audio(effective_path)
        return g.strip() if g else None

    if WHATSAPP_TRANSCRIBE_LOCAL_FIRST:
        logger.info("[%s] Orden transcripción: Whisper local → N8N/API → Gemini", phone)
        t = _local()
        if t:
            return t
        t = _api()
        if t:
            return t
        return _gemini()

    t = _api()
    if t:
        return t
    t = _local()
    if t:
        return t
    return _gemini()


def _ask_ai(text: str, phone: str) -> str:
    """
    Backend único de IA para WhatsApp.
    Prioridad: Gemini si está configurado, si no Claude (para no romper instalaciones previas).
    """
    if _has_gemini_text:
        return _ask_gemini(text, phone)
    return _ask_claude(text, phone)


def _build_evolution_quote(key: dict, message: dict) -> dict | None:
    """
    Construye el bloque `quoted` para sendText (Evolution).
    Responder citando el mensaje entrante hace que el hilo sea el mismo en **móvil y PC**
    (especialmente en el chat “contigo mismo” / @lid).
    """
    if not key or not key.get("id"):
        return None
    rj = (key.get("remoteJid") or "").strip()
    mid = (key.get("id") or "").strip()
    if not rj or not mid:
        return None
    msg_part: dict = {}
    if isinstance(message, dict):
        if message.get("conversation") is not None:
            msg_part["conversation"] = str(message.get("conversation") or "")
        elif message.get("extendedTextMessage"):
            msg_part["extendedTextMessage"] = message["extendedTextMessage"]
        elif message.get("audioMessage"):
            msg_part["conversation"] = "[audio]"
        else:
            msg_part["conversation"] = ""
    else:
        msg_part["conversation"] = ""
    return {
        "key": {
            "remoteJid": rj,
            "fromMe": bool(key.get("fromMe")),
            "id": mid,
        },
        "message": msg_part,
    }


def _send(jid: str, text: str, *, reply_quote: dict | None = None) -> None:
    """Envía un mensaje de texto via Evolution API. `reply_quote` opcional para responder en el mismo hilo."""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        logger.warning("Evolution API no configurada; respuesta no enviada: %s", text[:80])
        return
    # Registrar para evitar procesar el eco fromMe
    _sent_texts.append((jid, text.strip(), datetime.now()))
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    def _post_once(target_jid: str) -> None:
        # Evolution espera `number` en dígitos (54…); si mandamos JID completo a veces responde mal.
        num_digits = re.sub(r"\D", "", target_jid.split("@", 1)[0] if target_jid else "")
        if not num_digits:
            raise ValueError(f"JID inválido para envío: {target_jid!r}")
        body: dict = {"number": num_digits, "textMessage": {"text": text}}
        if reply_quote:
            body["quoted"] = reply_quote
        resp = httpx.post(
            url,
            json=body,
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.error(
                "Evolution sendText HTTP %s number=%s body=%s",
                resp.status_code,
                num_digits,
                (resp.text or "")[:900],
            )
        resp.raise_for_status()

    try:
        _post_once(jid)
        return
    except Exception as e:
        logger.error("Error enviando WhatsApp a %s: %s", jid, e)

    # Fallback de compatibilidad: algunos números alternan entre formato local y 549...
    # según el estado de la sesión en Evolution.
    try:
        if not jid.endswith("@s.whatsapp.net"):
            return
        num = jid.replace("@s.whatsapp.net", "")
        alt = None
        if num in WHATSAPP_PHONE_MAP:
            alt = WHATSAPP_PHONE_MAP.get(num)
        else:
            for k, v in WHATSAPP_PHONE_MAP.items():
                if v == num:
                    alt = k
                    break
        if not alt or alt == num:
            return
        alt_jid = f"{alt}@s.whatsapp.net"
        _post_once(alt_jid)
        logger.info("Fallback de envío exitoso: %s -> %s", jid, alt_jid)
    except Exception as e2:
        logger.error("Fallback también falló para %s: %s", jid, e2)


def _mime_for_image_bytes(image_bytes: bytes, file_name: str) -> str:
    """MIME según firma de bytes o extensión (DALL·E suele PNG; Imagen puede variar)."""
    if len(image_bytes) >= 8 and image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(image_bytes) >= 3 and image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if file_name.lower().endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if file_name.lower().endswith(".webp"):
        return "image/webp"
    return "image/png"


def _prepare_image_bytes_for_evolution(image_bytes: bytes, *, file_name: str) -> tuple[bytes, str]:
    """
    Evolution API suele rechazar o no mostrar en WhatsApp imágenes muy grandes en JSON base64.
    Comprime JPEG si hace falta; alinea extensión con el tipo real (DRR = JPEG).
    """
    from io import BytesIO

    mime = _mime_for_image_bytes(image_bytes, file_name)
    max_payload = int(os.getenv("WHATSAPP_IMAGE_MAX_BYTES", "1800000"))
    if mime != "image/jpeg" or len(image_bytes) <= max_payload:
        return image_bytes, file_name

    try:
        from PIL import Image

        im = Image.open(BytesIO(image_bytes))
        im = im.convert("RGB")
        out = BytesIO()
        q = 88
        data = image_bytes
        while q >= 50:
            out.seek(0)
            out.truncate(0)
            im.save(out, format="JPEG", quality=q, optimize=True)
            data = out.getvalue()
            if len(data) <= max_payload:
                logger.info(
                    "Imagen DRR comprimida para WhatsApp: %s → %s bytes (q=%s)",
                    len(image_bytes),
                    len(data),
                    q,
                )
                return data, "producto_drr.jpg"
            q -= 7
        w, h = im.size
        while len(data) > max_payload and min(w, h) > 400:
            w = max(int(w * 0.82), 400)
            h = max(int(h * 0.82), 400)
            im2 = im.resize((w, h), Image.Resampling.LANCZOS)
            out.seek(0)
            out.truncate(0)
            im2.save(out, format="JPEG", quality=80, optimize=True)
            data = out.getvalue()
            im = im2
        logger.info(
            "Imagen DRR redimensionada/comprimida para WhatsApp: %s bytes finales",
            len(data),
        )
        return data, "producto_drr.jpg"
    except Exception as e:
        logger.warning("No se pudo comprimir JPEG para Evolution (%s); se intenta el original.", e)
        return image_bytes, "producto_drr.jpg"


def _send_media_image(jid: str, image_bytes: bytes, caption: str | None = None, file_name: str = "jarvis.png") -> bool:
    """
    Envía una imagen por Evolution API (POST /message/sendMedia/{instance}).

    Varias builds de Evolution validan el cuerpo anidado `mediaMessage` (no el formato plano
    de la doc v2 genérica). Sin ese objeto devuelve: instance requires property \"mediaMessage\".
    """
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        logger.warning("Evolution API no configurada; imagen no enviada")
        return False
    num = jid.replace("@s.whatsapp.net", "").replace("@g.us", "").lstrip("+")
    num = re.sub(r"\D", "", num)
    if not num:
        return False
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendMedia/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY}
    caption_s = (caption or "")[:1024]
    mime = _mime_for_image_bytes(image_bytes, file_name)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    # Evolution valida enum en minúsculas: image | document | video | audio (no "Image").
    mediatype = (os.getenv("WHATSAPP_EVOLUTION_MEDIATYPE", "image") or "image").strip().lower()
    if mediatype not in ("image", "document", "video", "audio"):
        mediatype = "image"
    body = {
        "number": num,
        "mediaMessage": {
            "mediatype": mediatype,
            "mimetype": mime,
            "caption": caption_s,
            "media": b64,
            "fileName": file_name,
        },
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=120.0)
        if resp.status_code >= 400:
            logger.error(
                "Evolution sendMedia HTTP %s: %s",
                resp.status_code,
                (resp.text or "")[:800],
            )
        resp.raise_for_status()
        _sent_texts.append((jid, caption_s[:80], datetime.now()))
        logger.info("Imagen enviada por WhatsApp a %s (%s bytes payload)", jid, len(image_bytes))
        return True
    except Exception as e:
        logger.exception("Error enviando imagen WhatsApp a %s: %s", jid, e)
        return False


def _download_image_bytes(url: str) -> bytes | None:
    """Descarga una imagen desde URL (User-Agent de navegador; APIs/CDN suelen bloquear bots)."""
    try:
        from drr.web_image_search import download_image_url

        return download_image_url(url, max_bytes=20 * 1024 * 1024)
    except Exception as e:
        logger.warning("Error descargando imagen %s: %s", url[:60], e)
        return None


def _search_web_image_and_download_wa(
    query: str,
    max_size: int = 20 * 1024 * 1024,
    *,
    allow_ai_fallback: bool = False,
) -> tuple[bytes | None, str]:
    """BUSCAR_IMAGEN: DuckDuckGo. La generación IA solo si allow_ai_fallback (usuario lo pidió explícito)."""
    from drr.web_image_search import search_web_image_bytes

    img_bytes, mime = search_web_image_bytes(query, max_size=max_size)
    if img_bytes:
        return img_bytes, mime
    if allow_ai_fallback and (GEMINI_API_KEY or OPENAI_API_KEY):
        logger.info(
            "BUSCAR_IMAGEN: sin resultados web para %r; respaldo con imagen generada (usuario pidió generación explícita)",
            (query or "")[:80],
        )
        from drr.image_generate_env import generate_image_bytes_env

        gen = generate_image_bytes_env(
            f"Fotografía o ilustración realista, un solo encuadre claro, tema: {query}"
        )
        if gen:
            return gen, "image/png"
    return None, ""


def _generate_image_bytes_wa(prompt: str) -> bytes | None:
    """Genera imagen con Gemini Imagen o OpenAI DALL·E 3 (lee claves del entorno)."""
    from drr.image_generate_env import generate_image_bytes_env

    return generate_image_bytes_env(prompt)


def _parse_product_prefs_from_user_text(user_text: str) -> dict:
    """
    Interpreta preferencias de filtros para DRR desde el texto del usuario.
    Devuelve dict con claves:
      - limit (int | None)
      - include_prices (bool | None)
      - order (\"last_modified_desc\" | \"last_modified_asc\" | None)
      - solo_lista_precio_id (int | None): si el usuario pide una sola lista de precio.
    """
    t = (user_text or "").lower()

    # Cantidad (solo números). Si viene en palabras (p.ej. "cinco") lo dejamos a la IA / reply.
    limit = None
    m = re.search(r"(?:^|\b)(\d{1,3})\s*(?:productos|producto)\b", t)
    if not m:
        m = re.search(r"(?:traeme|dame|ponme|llévame)\s*(\d{1,3})\b", t)
    if m:
        try:
            limit = int(m.group(1))
        except Exception:
            limit = None

    include_prices = None
    if "sin precio" in t or "sin precios" in t or "no precio" in t:
        include_prices = False
    elif "con precio" in t or "con precios" in t or "con los precios" in t:
        include_prices = True
    elif "precio" in t and "sin precio" not in t and "sin precios" not in t:
        # Si menciona "precio" sin negar explícitamente, asumimos que los quiere.
        include_prices = True

    order = None
    wants_last_modified = any(
        kw in t
        for kw in (
            "ultima modific",
            "última modific",
            "ultima actualiz",
            "última actualiz",
            "fecha",
            "recientes",
            "más recientes",
            "ultimos",
            "últimos",
        )
    )
    if wants_last_modified:
        order = "last_modified_desc"
        if any(kw in t for kw in ("asc", "viejos", "más viejos", "antigu")):
            order = "last_modified_asc"

    solo_lista_precio_id = None
    m_lp = re.search(
        r"(?:lista\s+(?:de\s+)?precio|listaprecio|precio\s+lista)\s*(?:n[°º]?\s*|id\s*|#\s*)?(\d{1,4})\b",
        t,
    )
    if m_lp:
        try:
            solo_lista_precio_id = int(m_lp.group(1))
        except ValueError:
            solo_lista_precio_id = None
    if solo_lista_precio_id is None:
        m_sl = re.search(
            r"\b(?:solo|únicamente|unicamente|solamente)\s+(?:la\s+)?lista\s+(?:de\s+precio\s+)?(\d{1,4})\b",
            t,
        )
        if m_sl:
            try:
                solo_lista_precio_id = int(m_sl.group(1))
            except ValueError:
                solo_lista_precio_id = None

    return {
        "limit": limit,
        "include_prices": include_prices,
        "order": order,
        "solo_lista_precio_id": solo_lista_precio_id,
    }


def _extract_last_modified_dt(extra: dict) -> datetime | None:
    """
    Intenta extraer una fecha de última modificación desde campos extra del Producto.
    """
    if not isinstance(extra, dict):
        return None

    # Primero, intentamos por claves conocidas.
    candidate_keys = (
        "fechaultimamodificacion",
        "ultimamodificacion",
        "fechaultimaactualizacion",
        "ultimaactualizacion",
        "lastmodified",
        "modifiedat",
        "updatedat",
        "updated_at",
        "fechamodificacion",
        "fecha_actualizacion",
        "fecha_actualizada",
    )

    value = None
    extra_lower = {str(k).lower(): k for k in extra.keys()}
    for ck in candidate_keys:
        real_key = extra_lower.get(ck)
        if real_key is None:
            continue
        v = extra.get(real_key)
        if v not in (None, ""):
            value = v
            break

    # Si no encontramos, hacemos match heurístico por nombre de clave.
    if value is None:
        for k, v in extra.items():
            if v in (None, ""):
                continue
            kl = str(k).lower()
            if "ultima" in kl and ("modif" in kl or "actualiz" in kl or "update" in kl):
                value = v
                break
            if "last" in kl and ("modif" in kl or "modified" in kl or "update" in kl):
                value = v
                break
            if ("modified" in kl or "updated" in kl) and "at" in kl:
                value = v
                break

    if value is None:
        return None

    try:
        # Epoch seconds.
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value))
        s = str(value).strip()
        if not s:
            return None
        # ISO (con o sin Z).
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        pass

    # Formatos alternativos comunes: DD/MM/YYYY o MM/DD/YYYY
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except Exception:
            continue

    return None


def _sort_products_by_last_modified(productos: list, order: str) -> list:
    reverse = order == "last_modified_desc"

    def _key(p):
        dt = _extract_last_modified_dt(getattr(p, "extra", None) or {})
        # None al final.
        return dt or (datetime.min if reverse else datetime.max)

    try:
        return sorted(productos, key=_key, reverse=reverse)
    except Exception:
        return productos


def _query_drr_productos(
    descripcion: str = "",
    limit: int = 5,
    *,
    include_prices: bool = True,
    order: str | None = None,
    solo_lista_precio_id: int | None = None,
) -> tuple[str, list]:
    """
    Consulta la API DRR. Devuelve (texto formateado, lista de Producto en el orden mostrado).
    La segunda lista tiene hasta `limit` ítems (para imagen del producto 1 = índice 1).
    """
    if not DRR_API_BASE_URL:
        return "(DRR no configurado)", []
    try:
        from drr.api_client import DRRProductoAPIClient
        from drr.formatter import linea_producto_resumen

        repo = DRRProductoAPIClient(DRR_API_BASE_URL, api_key=DRR_API_KEY or None, cache_ttl_seconds=25)
        fetch_limit = limit if order is None else max(limit, 50)
        productos = repo.listar(descripcion=descripcion or None, limit=fetch_limit)
        if not productos:
            return f"(Sin productos encontrados para: {descripcion or 'todos'})", []

        total = len(productos)
        out_list = productos
        if order in ("last_modified_desc", "last_modified_asc"):
            out_list = _sort_products_by_last_modified(productos, order)

        shown = out_list[:limit]
        nombres: dict[int, str] = {}
        if include_prices:
            from drr.lista_precios import nombres_listas_precio

            nombres = nombres_listas_precio(DRR_API_BASE_URL, DRR_API_KEY or None)
        lines = [
            linea_producto_resumen(
                p,
                include_prices=include_prices,
                nombres_lista_precio=nombres if include_prices else None,
                solo_lista_precio_id=solo_lista_precio_id,
            )
            for p in shown
        ]

        result = "\n\n".join(lines)
        if total > limit:
            result += f"\n\n_(mostrando {limit} de {total})_"
        return result, shown
    except Exception as e:
        return f"(Error DRR: {e})", []


def _get_productos(
    descripcion: str = "",
    limit: int = 5,
    *,
    include_prices: bool = True,
    order: str | None = None,
    solo_lista_precio_id: int | None = None,
) -> str:
    """Consulta la API DRR y devuelve solo el texto (compatibilidad)."""
    text, _ = _query_drr_productos(
        descripcion=descripcion,
        limit=limit,
        include_prices=include_prices,
        order=order,
        solo_lista_precio_id=solo_lista_precio_id,
    )
    return text


def _send_drr_product_image_wa(jid: str, phone: str, idx_1based: int, send) -> bool:
    """Envía la imagen del producto en la posición idx_1based del último listado DRR."""
    snap_list = _last_drr_products_pick(phone)
    if not snap_list:
        send("❌ No tengo un listado de productos reciente. Pedime primero productos (ej. «traeme 5 martillos»).")
        return False
    if idx_1based < 1 or idx_1based > len(snap_list):
        send(f"❌ En el último listado solo hay {len(snap_list)} producto(s). Pedí un número entre 1 y {len(snap_list)}.")
        return False
    row = snap_list[idx_1based - 1]
    send("🖼 Descargando imagen del producto…")
    from drr.api_client import fetch_product_image_bytes_for_snapshot

    if not DRR_API_BASE_URL:
        send("❌ DRR no está configurado (DRR_API_BASE_URL).")
        return False
    img_bytes = fetch_product_image_bytes_for_snapshot(
        row,
        base_url=DRR_API_BASE_URL,
        api_key=DRR_API_KEY or None,
    )
    if not img_bytes:
        send(
            "ℹ️ Este producto no tiene imagen en el sistema DRR (o la referencia no se pudo descargar). "
            "No genero imágenes de reemplazo; pedí otro producto o cargá la foto en el catálogo."
        )
        return False
    img_ready, fname = _prepare_image_bytes_for_evolution(img_bytes, file_name="producto_drr.jpg")
    desc = (row.get("descripcion") or "")[:180]
    cap = f"Producto {idx_1based}: {desc}" if desc else f"Producto {idx_1based}"
    if _send_media_image(jid, img_ready, caption=cap, file_name=fname):
        mime = _mime_for_image_bytes(img_ready, fname)
        _last_image_wa_put(phone, {"bytes": img_ready, "mime_type": mime})
        try:
            cid = int(row["id"])
        except (TypeError, ValueError):
            cid = None
        if cid:
            _last_drr_imagen_context_put(
                phone,
                {
                    "codigo_id": cid,
                    "idx": idx_1based,
                    "descripcion": (row.get("descripcion") or "")[:220],
                },
            )
        return True
    send("❌ No pude enviar la imagen por WhatsApp.")
    return False


def _upload_drr_product_to_meta_catalog_wa(phone: str, idx_1based: int, send) -> None:
    """
    Toma el ítem ``idx_1based`` del último listado DRR en memoria y lo crea en el catálogo
    de Meta (Graph API). No usa Evolution para el alta: solo envía HTTPS a graph.facebook.com.

    Requiere META_ACCESS_TOKEN, META_PRODUCT_CATALOG_ID y una imagen con URL pública (ver ``drr/meta_catalog.py``).
    """
    from drr.meta_catalog import meta_catalog_upload_configured, upload_product_from_snapshot

    if not DRR_API_BASE_URL:
        send("❌ DRR no está configurado (DRR_API_BASE_URL).")
        return
    if not meta_catalog_upload_configured():
        send(
            "❌ Para subir productos al catálogo de WhatsApp hace falta configurar en el servidor "
            "META_ACCESS_TOKEN y META_PRODUCT_CATALOG_ID (catálogo de Meta vinculado a tu negocio). "
            "Ver comentarios en `drr/meta_catalog.py`."
        )
        return

    snap_list = _last_drr_products_pick(phone)
    if not snap_list:
        send(
            "❌ No tengo un listado de productos reciente. Pedime primero productos "
            "(ej. «traeme 5 martillos») y después «subir el primer producto al catálogo»."
        )
        return
    if idx_1based < 1 or idx_1based > len(snap_list):
        send(f"❌ En el último listado solo hay {len(snap_list)} producto(s). Pedí un número entre 1 y {len(snap_list)}.")
        return

    row = snap_list[idx_1based - 1]
    send(f"📤 Enviando producto {idx_1based} al catálogo Meta (WhatsApp)…")
    ok, detail = upload_product_from_snapshot(row, drr_base_url=DRR_API_BASE_URL)
    if ok:
        send(f"✅ {detail}")
    else:
        send(f"❌ {detail}")


def _save_last_image_to_drr_product_wa(jid: str, phone: str, save_idx: int, send) -> None:
    """
    PATCH /Producto con la última imagen en memoria.
    save_idx: -1 = usar codigoID del último producto del que se mostró foto; >=1 = ítem del último listado.
    """
    if not DRR_API_BASE_URL:
        send("❌ DRR no está configurado (DRR_API_BASE_URL).")
        return
    data = _last_image_wa_pick(phone)
    if not data or not data.get("bytes"):
        send("No tengo ninguna imagen reciente para guardar. Pedí la foto del producto, editá si querés, y volvé a pedir guardar.")
        return

    codigo_id: int | None = None
    label = ""
    if save_idx >= 1:
        snap_list = _last_drr_products_pick(phone)
        if not snap_list or save_idx > len(snap_list):
            send(
                f"❌ Para guardar en el producto {save_idx} necesito un listado reciente con al menos ese ítem. Pedime productos de nuevo."
            )
            return
        row = snap_list[save_idx - 1]
        try:
            codigo_id = int(row["id"])
        except (TypeError, ValueError):
            codigo_id = None
        label = (row.get("descripcion") or "")[:80]
    else:
        ctx = _last_drr_imagen_context_pick(phone)
        if ctx:
            try:
                codigo_id = int(ctx["codigo_id"])
            except (TypeError, ValueError):
                codigo_id = None
            label = str(ctx.get("descripcion") or "")[:80]

    if not codigo_id:
        send(
            "No tengo un producto DRR asociado a esta imagen. "
            "Pedime la imagen desde el listado (ej. «foto del primero»), editá si hace falta, y decime "
            "«guardá la imagen en el producto» o «guardá los cambios»."
        )
        return

    try:
        from drr.api_client import DRRProductoAPIClient

        client = DRRProductoAPIClient(
            DRR_API_BASE_URL.rstrip("/"),
            api_key=DRR_API_KEY or None,
            timeout=60,
        )
        b64 = DRRProductoAPIClient.imagen_bytes_para_patch(data["bytes"])
        send(f"💾 Guardando imagen en DRR (producto codigoID={codigo_id})…")
        ok, detail = client.patch_producto({"codigoID": codigo_id, "imagen": b64})
        if ok:
            extra = f" ({label})" if label else ""
            send(f"✅ Imagen actualizada en el producto{extra}. codigoID={codigo_id}.")
            logger.info("[%s] DRR PATCH imagen OK codigoID=%s bytes=%s", phone, codigo_id, len(data["bytes"]))
            return
        send(f"❌ No se pudo guardar en DRR: {detail[:1500]}")
    except Exception as e:
        logger.exception("DRR PATCH imagen: %s", e)
        send(f"❌ Error al guardar en DRR: {e}")


def _do_edit_image_wa(jid: str, phone: str, prompt: str, send) -> None:
    """Edita la última imagen con Gemini (Imagen edit + fallback modelo con salida de imagen)."""
    if not GEMINI_API_KEY:
        send("❌ Para editar imágenes hace falta GEMINI_API_KEY en el servidor.")
        return
    data = _last_image_wa_pick(phone)
    if not data or not data.get("bytes"):
        send(
            "No tengo ninguna imagen reciente para editar. Pedí una imagen (DRR, búsqueda o generación) y después decime cómo editarla."
        )
        return
    prompt = (prompt or "").strip()[:1000]
    if not prompt:
        send("Escribí qué cambio querés (ej: cambia el fondo a una playa).")
        return
    send("🖼 Editando imagen con Gemini…")
    try:
        from drr.gemini_image_edit import gemini_edit_image_bytes

        img_bytes = data["bytes"]
        mime = data.get("mime_type") or "image/png"
        out_bytes = gemini_edit_image_bytes(
            api_key=GEMINI_API_KEY,
            image_bytes=img_bytes,
            mime_type=mime,
            prompt=prompt,
        )
        if out_bytes and _send_media_image(jid, out_bytes, caption=prompt[:200], file_name="jarvis_edit.png"):
            _last_image_wa_put(phone, {"bytes": out_bytes, "mime_type": "image/png"})
            return
    except Exception as e:
        logger.exception("Error editando imagen (WA): %s", e)
        send(f"❌ No pude editar la imagen: {e}")
        return
    send("❌ No pude generar la imagen editada.")


def _tts_mp3_bytes(text: str) -> bytes | None:
    """TTS con edge-tts (misma voz que Telegram)."""
    text = (text or "").strip()[:2000]
    if not text:
        return None
    try:
        import tempfile

        import edge_tts

        async def _save() -> bytes:
            communicate = edge_tts.Communicate(text, voice="es-AR-ElenaNeural")
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                path = tmp.name
            try:
                await communicate.save(path)
                return Path(path).read_bytes()
            finally:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

        return asyncio.run(_save())
    except Exception as e:
        logger.exception("Error TTS (WA): %s", e)
        return None


def _send_audio_whatsapp(jid: str, audio_bytes: bytes, file_name: str = "jarvis.mp3") -> bool:
    """Envía un MP3 por Evolution (misma forma anidada que imágenes)."""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        logger.warning("Evolution API no configurada; audio no enviado")
        return False
    num = jid.replace("@s.whatsapp.net", "").replace("@g.us", "").lstrip("+")
    num = re.sub(r"\D", "", num)
    if not num:
        return False
    url = f"{EVOLUTION_API_URL.rstrip('/')}/message/sendMedia/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY}
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    body = {
        "number": num,
        "mediaMessage": {
            "mediatype": "audio",
            "mimetype": "audio/mpeg",
            "caption": "",
            "media": b64,
            "fileName": file_name,
        },
    }
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=120.0)
        resp.raise_for_status()
        logger.info("Audio enviado por WhatsApp a %s (%s bytes)", jid, len(audio_bytes))
        return True
    except Exception as e:
        logger.exception("Error enviando audio WhatsApp a %s: %s", jid, e)
        return False


def _run_command(cmd: str) -> str:
    """Ejecuta un comando en el servidor usando server_executor."""
    from server_executor import default_executor
    result = default_executor.run(cmd, timeout_seconds=60)
    return result.output or "(sin salida)"


def _search_web(query: str) -> str:
    """Búsqueda DuckDuckGo sincrónica."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "(Sin resultados)"
        lines = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()[:250]
            lines.append(f"{i}. {title}\n   {body}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"(Error al buscar: {e})"


def _wa_inbox_ensure_dirs() -> None:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("images", "videos", "documents", "audio"):
        (WA_INBOX_MEDIA / sub).mkdir(parents=True, exist_ok=True)


def _inbox_allowed_path(rel: str) -> Path | None:
    """Devuelve Path resuelto bajo BASE_DIR solo si está en notes/ o wa_inbox/."""
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    p = (BASE_DIR / rel).resolve()
    try:
        p.relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    notes_r = NOTES_DIR.resolve()
    inbox_r = WA_INBOX_ROOT.resolve()
    try:
        p.relative_to(notes_r)
        return p
    except ValueError:
        pass
    try:
        p.relative_to(inbox_r)
        return p
    except ValueError:
        return None


def _inbox_kind_from_rel(rel: str) -> str:
    r = rel.replace("\\", "/")
    if r.startswith("notes/"):
        return "note"
    if "/images/" in r:
        return "image"
    if "/videos/" in r:
        return "video"
    if "/audio/" in r:
        return "audio"
    return "document"


def _inbox_list_items() -> list[dict]:
    _wa_inbox_ensure_dirs()
    items: list[dict] = []
    for root in (NOTES_DIR, WA_INBOX_MEDIA):
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                rel = str(p.relative_to(BASE_DIR)).replace("\\", "/")
            except ValueError:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            items.append(
                {
                    "rel": rel,
                    "kind": _inbox_kind_from_rel(rel),
                    "name": p.name,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                }
            )
    items.sort(key=lambda x: -x["mtime"])
    return items


def _sanitize_inbox_filename(name: str, default: str = "file") -> str:
    base = Path(name or default).name
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or default
    return base[:120]


def _ext_from_mimetype(mime: str, fallback: str) -> str:
    m = (mime or "").split(";")[0].strip().lower()
    mp = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "application/pdf": ".pdf",
    }
    return mp.get(m, fallback)


def _download_evolution_media_object_bytes(
    media_obj: dict,
    media_key_id: str,
    phone: str,
    *,
    min_b64: int = 32,
) -> bytes | None:
    if media_key_id:
        b = _evolution_download_media_base64(media_key_id, phone=phone, min_bytes=min_b64)
        if b:
            return b
    media_url = None
    for url_field in ("downloadUrl", "fileUrl", "mediaUrl", "url"):
        val = media_obj.get(url_field)
        if val:
            media_url = val
            break
    if not media_url:
        return None
    headers: dict[str, str] = {}
    if EVOLUTION_API_KEY:
        headers["apikey"] = EVOLUTION_API_KEY
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(120.0, connect=20.0, read=120.0),
        ) as client:
            r = client.get(media_url, headers=headers)
            r.raise_for_status()
            data = r.content
            if len(data) < min_b64:
                return None
            return data
    except Exception as e:
        logger.warning("[%s] GET media Evolution falló: %s", phone, e)
        return None


def _media_key_for_object(media_obj: dict, fallback_key: str) -> str:
    ctx = media_obj.get("contextInfo") or {}
    stanza = (ctx.get("stanzaId") or ctx.get("stanza_id") or "").strip()
    return (fallback_key or stanza or "").strip()


def _persist_whatsapp_inbox_media(message: dict, media_key_id: str, phone: str) -> str | None:
    """
    Guarda imagen / video / documento entrante en wa_inbox/media (sin pedir confirmación).
    No maneja notas de voz (eso sigue el flujo de transcripción).
    """
    if message.get("audioMessage") or message.get("voiceMessage") or message.get("ptt"):
        return None
    doc = message.get("documentMessage") or {}
    if isinstance(doc, dict) and (doc.get("mimetype") or "").lower().startswith("audio/"):
        return None

    _wa_inbox_ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    img = message.get("imageMessage")
    if isinstance(img, dict) and img:
        mk = _media_key_for_object(img, media_key_id)
        raw = _download_evolution_media_object_bytes(img, mk, phone)
        if not raw:
            return None
        mime = (img.get("mimetype") or "image/jpeg").lower()
        ext = _ext_from_mimetype(mime, ".jpg")
        fname = f"{ts}_{_sanitize_inbox_filename(img.get('fileName') or f'image{ext}', 'image')}"
        if not fname.lower().endswith(ext):
            fname += ext
        dest = WA_INBOX_MEDIA / "images" / fname
        dest.write_bytes(raw)
        rel = str(dest.relative_to(BASE_DIR)).replace("\\", "/")
        logger.info("[%s] Inbox imagen guardada %s (%s bytes)", phone, rel, len(raw))
        return rel

    vid = message.get("videoMessage")
    if isinstance(vid, dict) and vid:
        mk = _media_key_for_object(vid, media_key_id)
        raw = _download_evolution_media_object_bytes(vid, mk, phone)
        if not raw:
            return None
        mime = (vid.get("mimetype") or "video/mp4").lower()
        ext = _ext_from_mimetype(mime, ".mp4")
        fname = f"{ts}_{_sanitize_inbox_filename(vid.get('fileName') or f'video{ext}', 'video')}"
        if not fname.lower().endswith(ext):
            fname += ext
        dest = WA_INBOX_MEDIA / "videos" / fname
        dest.write_bytes(raw)
        rel = str(dest.relative_to(BASE_DIR)).replace("\\", "/")
        logger.info("[%s] Inbox video guardado %s (%s bytes)", phone, rel, len(raw))
        return rel

    if isinstance(doc, dict) and doc:
        mk = _media_key_for_object(doc, media_key_id)
        raw = _download_evolution_media_object_bytes(doc, mk, phone, min_b64=16)
        if not raw:
            return None
        mime = (doc.get("mimetype") or "application/octet-stream").lower()
        ext = _ext_from_mimetype(mime, Path(doc.get("fileName") or "").suffix or ".bin")
        base_name = doc.get("fileName") or f"doc{ext}"
        fname = f"{ts}_{_sanitize_inbox_filename(base_name, 'doc')}"
        if not fname.lower().endswith(ext) and ext != ".bin":
            fname += ext
        dest = WA_INBOX_MEDIA / "documents" / fname
        dest.write_bytes(raw)
        rel = str(dest.relative_to(BASE_DIR)).replace("\\", "/")
        logger.info("[%s] Inbox documento guardado %s (%s bytes)", phone, rel, len(raw))
        return rel

    return None


def _inbox_copy_path_to_audio_subdir(src: Path, phone: str, original_name: str) -> str | None:
    """Copia un archivo de audio ya descargado a wa_inbox/media/audio."""
    try:
        if not src.is_file():
            return None
        _wa_inbox_ensure_dirs()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suf = src.suffix.lower() or ".ogg"
        dest_n = f"{ts}_{_sanitize_inbox_filename(original_name, 'voice')}"
        if not dest_n.lower().endswith(suf):
            dest_n += suf
        dest = WA_INBOX_MEDIA / "audio" / dest_n
        shutil.copy2(src, dest)
        rel = str(dest.relative_to(BASE_DIR)).replace("\\", "/")
        logger.info("[%s] Inbox audio archivado %s", phone, rel)
        return rel
    except Exception as e:
        logger.warning("[%s] No se pudo archivar audio en inbox: %s", phone, e)
        return None


def _save_note_file(note_body: str) -> tuple[str, str]:
    """Escribe nota en notes/note_*.txt. Devuelve (rel_path, nombre_archivo)."""
    _wa_inbox_ensure_dirs()
    body = (note_body or "").strip()
    fname = f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path = NOTES_DIR / fname
    path.write_text(body, encoding="utf-8")
    rel = str(path.relative_to(BASE_DIR)).replace("\\", "/")
    return rel, fname


def _format_notes_list_for_whatsapp(limit: int = 25) -> str:
    _wa_inbox_ensure_dirs()
    files = sorted(NOTES_DIR.glob("note_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    if not files:
        return "📋 No hay notas en el servidor (carpeta notes/). Podés guardar con: «guardame esto: …» o el modelo responde NOTA: …"
    lines: list[str] = ["📋 Últimas notas:"]
    for p in files:
        try:
            snippet = p.read_text(encoding="utf-8", errors="replace").strip().replace("\n", " ")[:180]
        except OSError as e:
            snippet = f"(error: {e})"
        lines.append(f"• {p.name}\n  {snippet}")
    lines.append("\nPanel web: /admin/inbox (mismo host que el bridge, token Bearer = ADMIN_PANEL_TOKEN).")
    return "\n\n".join(lines)


def _user_wants_list_notes(text: str) -> bool:
    t = (text or "").lower().strip()
    phrases = (
        "mostrar notas",
        "mostrame las notas",
        "mostrame todas las notas",
        "todas las notas",
        "listar notas",
        "listado de notas",
        "ver notas",
        "ver las notas",
        "mis notas",
        "notas guardadas",
        "qué notas",
        "que notas",
        "cuales notas",
        "cuáles notas",
    )
    return any(p in t for p in phrases)


def _user_text_extract_save_note(text: str) -> str | None:
    """
    Si el usuario pide guardar una nota en lenguaje natural, devuelve el cuerpo; si no, None.
    No requiere confirmación SI (evita CMD: cat ~/.jarvis_notes.txt u otras rutas inventadas).
    """
    raw = (text or "").strip()
    if not raw:
        return None
    patterns = [
        r"^(?:/nota)\s+(.+)$",
        r"^(?:guardame|guardá|guarda)\s+esto\s*:\s*(.+)$",
        r"^(?:anotá|anota|nota)\s*:\s*(.+)$",
        r"^(?:guardar\s+nota|guardá\s+nota|guarda\s+nota)\s*:\s*(.+)$",
        r"^(?:recordame|recordá|recuerdame|recuerda)\s*:\s*(.+)$",
    ]
    for pat in patterns:
        m = re.match(pat, raw, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    # Frases tipo: guardame esto "..." o '...'
    m2 = re.match(
        r"^(?:guardame|guardá|guarda)\s+esto\s+[\"“](.+)[\"”]\s*$",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m2:
        return m2.group(1).strip()
    return None


def _process_message(text: str, phone: str, jid: str, *, reply_quote: dict | None = None) -> None:
    """
    Procesa un mensaje de texto:
    - Verifica sesión / autenticación
    - Detecta confirmación de comandos pendientes (SI/NO)
    - Llama a la IA y parsea prefijos NOTA:, ACCION:, CMD:, PRODUCTOS:, BUSCAR_IMAGEN:, IMAGEN:, BUSCAR:
    (Misma convención que `jarvis_bot.py` en Telegram.)

    reply_quote: si viene del webhook (mensaje citado), Evolution encadena la respuesta y suele
    verse bien también en el **celular** (chat contigo / LID).
    """

    def send(msg: str) -> None:
        _send(jid, msg, reply_quote=reply_quote)

    # Remapeamos el `jid` para enviar usando el número que Evolution acepta.
    # En algunos flujos (p.ej. al activar sesión) el webhook puede pasar un `jid`
    # distinto al que Evolution permite enviar, y eso deja el cliente en
    # "esperando mensaje" porque la respuesta no llega.
    try:
        phone_norm = re.sub(r"\D", "", phone or "")
        if phone_norm and phone_norm in WHATSAPP_PHONE_MAP:
            phone_send = WHATSAPP_PHONE_MAP.get(phone_norm, phone_norm)
            if phone_send and jid and (phone_send != phone_norm):
                jid = f"{phone_send}@s.whatsapp.net"
    except Exception:
        pass

    # --- Autenticación ---
    if not _session_ok(phone):
        t = text.strip()
        # Compatibilidad: aceptar tanto "enviar clave" como "/login TU_CLAVE"
        # (Telegram usa /login, WhatsApp históricamente aceptaba solo la clave a pelo).
        candidate = t
        m = re.match(r"^/login\s+(.+)\s*$", t, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()

        if candidate == AGENT_SECRET:
            _open_session(phone)
            send(f"✅ Sesión iniciada. Hola, soy Jarvis. Sesión válida por {SESSION_HOURS}h.")
        else:
            send("🔒 Ingresá la clave de acceso para usar Jarvis. Podés enviarla a pelo o como `/login TU_CLAVE`.")
        return

    # --- Confirmación de comando pendiente ---
    if phone in _pending_cmd:
        cmd = _pending_cmd.pop(phone)
        if text.strip().upper() in ("SI", "SÍ", "S", "YES", "Y"):
            logger.info("[%s] Confirmación recibida; ejecutando CMD=%r", phone, cmd)
            send(f"⚙️ Ejecutando: `{cmd}`")
            output = _run_command(cmd)
            logger.info("[%s] Ejecutado. output_len=%s output_head=%r", phone, len(output or ""), (output or "")[:120])
            send(f"✅ Resultado:\n```\n{output[:3000]}\n```")
        else:
            send("❌ Comando cancelado.")
        return

    # --- Notas del usuario: guardar / listar sin confirmación SI (no pasan por CMD:) ---
    early_note = _user_text_extract_save_note(text)
    if early_note:
        _, fn = _save_note_file(early_note)
        send(f"📝 Nota guardada ({fn}).\n\n{early_note[:3500]}")
        return

    if _user_wants_list_notes(text):
        send(_format_notes_list_for_whatsapp())
        return

    # --- Guardar imagen (editada o no) en DRR vía PATCH /Producto (Swagger: ProductoPatchRequest) ---
    save_idx = parse_save_image_to_drr_product_index(text.strip())
    if save_idx is not None:
        _save_last_image_to_drr_product_wa(jid, phone, save_idx, send)
        return

    # --- Subir un producto del listado DRR al catálogo de WhatsApp (Meta Graph API) ---
    cat_idx = parse_upload_whatsapp_catalog_index(text.strip())
    if cat_idx is not None:
        _upload_drr_product_to_meta_catalog_wa(phone, cat_idx, send)
        return

    # --- Editar última imagen (paridad con Telegram /editarimagen) ---
    edit_prompt_early = parse_edit_image_intent(text.strip())
    if edit_prompt_early:
        if _last_image_wa_pick(phone):
            _do_edit_image_wa(jid, phone, edit_prompt_early, send)
            return
        logger.info(
            "[%s] Intención EDITAR_IMAGEN sin última imagen en memoria (aliases=%s)",
            phone,
            sorted(_wa_phone_aliases(phone)),
        )
        send(
            "No tengo la imagen anterior en memoria para editarla. "
            "Volvé a pedir la imagen (búsqueda o generación) y enseguida la edición, "
            "sin reiniciar el servidor del bridge entre mensajes."
        )
        return

    # --- Imagen de un producto del último listado DRR (lenguaje natural) ---
    # Debe ir ANTES del «seguimiento» de edición: frases como «poné la imagen del primer producto»
    # matchean pon[eé] + imagen y no deben interpretarse como edición de la última foto.
    idx_natural = parse_producto_imagen_index(text)
    if idx_natural is not None and _last_drr_products_pick(phone):
        _send_drr_product_image_wa(jid, phone, idx_natural, send)
        return

    # --- Seguimiento: ya hay última imagen y el usuario pide un cambio visual sin decir «editame la imagen» ---
    if _last_image_wa_pick(phone) and not user_requested_ai_image_generation(text):
        follow_edit = parse_followup_last_image_edit_intent(text)
        if follow_edit:
            _do_edit_image_wa(jid, phone, follow_edit, send)
            return

    # --- IA de texto (Gemini preferido, luego Claude) ---
    logger.info("[%s] (%s) → %s", phone, BACKEND_NAME, text[:80])
    reply = _ask_ai(text, phone)
    logger.info("[%s] (%s) ← %s", phone, BACKEND_NAME, reply[:80])

    # El modelo a veces responde con comando estilo Telegram (/editarimagen) en vez de IMAGEN:.
    m_tg_edit = re.match(r"^/editarimagen\s+(.+)$", reply.strip(), flags=re.IGNORECASE | re.DOTALL)
    if m_tg_edit:
        edit_prompt = m_tg_edit.group(1).strip()
        if _last_image_wa_pick(phone):
            logger.info("[%s] Respuesta /editarimagen; ejecutando edición de última imagen.", phone)
            _do_edit_image_wa(jid, phone, edit_prompt, send)
            return
        logger.info("[%s] /editarimagen sin última imagen en memoria", phone)
        send(
            "No tengo la imagen anterior en memoria para editarla. "
            "Pedí de nuevo la imagen del producto y después la edición."
        )
        return

    # --- Parseo de prefijos especiales ---

    if reply.startswith("NOTA:"):
        note = reply.replace("NOTA:", "", 1).strip()
        _, fn = _save_note_file(note)
        send(f"📝 Nota guardada ({fn}):\n{note}")
        return

    list_head = reply.strip().splitlines()[0].strip() if reply.strip() else ""
    if list_head.upper().startswith("LISTAR_NOTAS"):
        send(_format_notes_list_for_whatsapp())
        return

    # Comandos propuestos por el modelo.
    # Se espera formato tipo (en líneas):
    # ACCION: <texto>
    # CMD: <comando>
    # Antes esto fallaba cuando el reply empezaba con ACCION: y el cmd quedaba mezclado.
    if ("ACCION:" in reply) or ("CMD:" in reply):
        action = None
        cmd = None
        for line in reply.splitlines():
            s = line.strip()
            if s.startswith("ACCION:"):
                action = s.replace("ACCION:", "", 1).strip()
            elif s.startswith("CMD:"):
                cmd = s.replace("CMD:", "", 1).strip()

        # Compat: si el reply empieza directo con CMD:
        if cmd is None and reply.strip().startswith("CMD:"):
            cmd = reply.strip().replace("CMD:", "", 1).strip()

        if cmd:
            _pending_cmd[phone] = cmd
            if action:
                send(
                    "⚠️ Jarvis quiere ejecutar:\n"
                    f"Acción: {action}\n"
                    f"CMD: `{cmd}`\n\n"
                    "Respondé *SI* para confirmar o cualquier otra cosa para cancelar.",
                )
            else:
                send(
                    f"⚠️ Jarvis quiere ejecutar:\nCMD: `{cmd}`\n\n"
                    "Respondé *SI* para confirmar o cualquier otra cosa para cancelar.",
                )
            return

    # DRR PRODUCTOS (tolerante a typo del modelo): "PRODUCTOS:" o "PRODUOTOS:"
    first_line = reply.strip().splitlines()[0].strip() if reply.strip() else ""
    first_line_clean = first_line.replace("```", "").strip()
    m_prod = re.match(r"^(PRODUCTOS|PRODUOTOS)\s*:\s*(.*)\s*$", first_line_clean, flags=re.IGNORECASE)
    if m_prod:
        query = (m_prod.group(2) or "").strip()
        try:
            limit = 5
            parts = query.split("|")
            desc = parts[0].strip()
            if len(parts) > 1 and parts[1].strip().isdigit():
                limit = min(int(parts[1].strip()), 20)
        except Exception:
            desc = query
            limit = 5

        prefs = _parse_product_prefs_from_user_text(text)
        final_limit = prefs.get("limit") if prefs.get("limit") is not None else limit
        final_include_prices = prefs.get("include_prices")
        if final_include_prices is None:
            final_include_prices = True
        final_order = prefs.get("order")
        final_solo_lista = prefs.get("solo_lista_precio_id")

        logger.info(
            "[%s] DRR filtros (from user audio/text): desc=%r limit=%s include_prices=%s order=%r solo_lista=%s",
            phone,
            desc,
            final_limit,
            final_include_prices,
            final_order,
            final_solo_lista,
        )

        send(f"📦 Buscando productos: {desc or 'todos'} (limit={final_limit})...")
        resultado, shown = _query_drr_productos(
            descripcion=desc,
            limit=final_limit,
            include_prices=final_include_prices,
            order=final_order,
            solo_lista_precio_id=final_solo_lista,
        )
        if shown:
            _last_drr_products_put(phone, [p.to_snapshot() for p in shown])
        else:
            _last_drr_products_put(phone, [])
        out_msg = f"📦 Productos DRR:\n{resultado}"
        if shown:
            out_msg += (
                "\n\n_Para ver la imagen guardada en la API de un producto, escribí por ejemplo: "
                "«imagen del producto 1» o «foto del primero»._"
            )
            # Catálogo Meta (opcional): si está configurado en el servidor, mostramos el comando.
            try:
                from drr.meta_catalog import meta_catalog_upload_configured

                if meta_catalog_upload_configured():
                    out_msg += (
                        "\n\n_Para subir un ítem de este listado al catálogo de WhatsApp (Meta), "
                        "por ejemplo: «subir el primer producto al catálogo» o «publicar producto 2 en whatsapp»._"
                    )
            except Exception:
                pass
        send(out_msg)
        return

    # --- Imágenes (alineado a Telegram: búsqueda web o generación IA) ---
    if "BUSCAR_IMAGEN:" in reply:
        m_img = re.search(r"BUSCAR_IMAGEN:\s*(.+)", reply, re.DOTALL)
        if m_img:
            query_img = m_img.group(1).strip().split("\n")[0].strip()
            if query_img:
                send("🔍 Buscando imagen en internet...")
                img_bytes, _mime = _search_web_image_and_download_wa(
                    query_img,
                    allow_ai_fallback=user_requested_ai_image_generation(text),
                )
                if img_bytes:
                    _last_image_wa_put(phone, {"bytes": img_bytes, "mime_type": _mime or "image/jpeg"})
                    _last_drr_imagen_context_put(phone, None)
                    if _send_media_image(jid, img_bytes, caption=f"Búsqueda: {query_img[:200]}"):
                        pass
                    return
                send(
                    "No encontré imágenes en la web para esa búsqueda. "
                    "Probá otra frase. "
                    "(No genero imágenes por IA salvo que pidas explícitamente «generame una imagen …».)"
                )
                return

    if "IMAGEN:" in reply:
        m_gen = re.search(r"IMAGEN:\s*(.+)", reply, re.DOTALL)
        if m_gen:
            prompt_imagen = m_gen.group(1).strip()
            # Evitar prompts basura cuando el modelo mezcla ayuda / otros prefijos
            if any(
                x in prompt_imagen
                for x in ("AUDIO:", "BUSCAR:", "CMD:", "NOTA:", "Logs", "Proyectos", "descripción detallada")
            ):
                send(reply[:4000])
                return
            explicit_gen = user_requested_ai_image_generation(text)
            last_img = _last_image_wa_pick(phone)
            if not explicit_gen and last_img:
                logger.info(
                    "[%s] Modelo devolvió IMAGEN: pero el usuario no pidió generación explícita; "
                    "interpretando como edición de la última imagen.",
                    phone,
                )
                _do_edit_image_wa(jid, phone, prompt_imagen[:1000], send)
                return
            if not explicit_gen:
                send(
                    "No genero imágenes nuevas a menos que lo pidas explícitamente "
                    "(por ejemplo: «generame una imagen de un gato sentado»). "
                    "Si querés modificar la última foto que te mandé, describí el cambio "
                    "(centrá el producto, agregá texto, etc.)."
                )
                return
            if not GEMINI_API_KEY and not OPENAI_API_KEY:
                send("❌ Falta API de imagen: configurá GEMINI_API_KEY u OPENAI_API_KEY en .env (igual que Telegram).")
                return
            send("🖼 Generando imagen...")
            out = _generate_image_bytes_wa(prompt_imagen)
            if out and _send_media_image(jid, out, caption=prompt_imagen[:200]):
                _last_image_wa_put(phone, {"bytes": out, "mime_type": "image/png"})
                _last_drr_imagen_context_put(phone, None)
                return
            send("❌ No se pudo generar la imagen.")
            return

    if "PRODUCTO_IMAGEN:" in reply.upper():
        m_pi = re.search(r"PRODUCTO_IMAGEN:\s*(\d+)", reply, flags=re.IGNORECASE)
        if m_pi:
            try:
                n = int(m_pi.group(1))
            except Exception:
                n = 0
            if n > 0:
                _send_drr_product_image_wa(jid, phone, n, send)
                return

    if "AUDIO:" in reply:
        m_au = re.search(r"AUDIO:\s*(.+)", reply, re.DOTALL)
        if m_au:
            texto_audio = m_au.group(1).strip()
            if any(
                x in texto_audio
                for x in ("IMAGEN:", "BUSCAR:", "CMD:", "NOTA:", "Logs", "Proyectos", "descripción detallada")
            ):
                send(reply[:4000])
                return
            send("🔊 Generando audio...")
            mp3 = _tts_mp3_bytes(texto_audio)
            if mp3 and _send_audio_whatsapp(jid, mp3):
                return
            send("❌ No se pudo generar o enviar el audio.")
            return

    if reply.startswith("BUSCAR:"):
        query = reply.replace("BUSCAR:", "", 1).strip()
        send(f"🔍 Buscando: {query}...")
        results = _search_web(query)
        prompt = (
            f"Resultados de búsqueda para '{query}':\n\n{results}\n\n"
            "Resumí o respondé en español según esta información."
        )
        final = _ask_ai(prompt, phone)
        send(final[:4000])
        return

    # Respuesta normal
    send(reply[:4000])


# =========================
# ENDPOINTS
# =========================


def _evolution_logout_apply_token(
    token: str, request: Request | None = None, instance: str = ""
) -> JSONResponse:
    """Respuesta JSON de logout Evolution; marca evolution_logout_applied para el panel."""
    if not (token or "").strip():
        return JSONResponse(
            {"ok": False, "detail": "Falta ?token= (AGENT_SECRET)"},
            status_code=400,
        )
    body: dict = {"token": token.strip()}
    if (instance or "").strip():
        body["instance"] = instance.strip()
    raw = json.dumps(body).encode("utf-8")
    hdrs = dict(request.headers) if request is not None else None
    code, payload = handle_logout_post_body(raw, hdrs)
    if isinstance(payload, dict):
        payload["evolution_logout_applied"] = True
    return JSONResponse(content=payload, status_code=code)


async def _evolution_logout_from_request_body(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    if isinstance(payload, dict):
        payload["evolution_logout_applied"] = True
    return JSONResponse(content=payload, status_code=code)


@app.get("/evo-logout")
def evolution_logout_via_query(
    request: Request,
    token: str = Query(default="", description="Misma clave que AGENT_SECRET"),
    instance: str = Query(default="", description="Nombre instancia Evolution (opcional)"),
):
    """
    Solo cierra sesión en Evolution (sin mezclar con el JSON de /status).
    Usá: GET /evo-logout?token=AGENT_SECRET
    """
    return _evolution_logout_apply_token(token, request, instance)


@app.post("/evo-logout")
async def evolution_logout_via_post(request: Request) -> JSONResponse:
    return await _evolution_logout_from_request_body(request)


# Mismas rutas bajo /admin: muchos nginx solo proxy_pass /admin/ al bridge ( /evo-logout da 404 ).
@app.get("/admin/evo-logout")
def admin_prefix_evo_logout_get(
    request: Request, token: str = Query(default=""), instance: str = Query(default="")
) -> JSONResponse:
    return _evolution_logout_apply_token(token, request, instance)


@app.post("/admin/evo-logout")
async def admin_prefix_evo_logout_post(request: Request) -> JSONResponse:
    return await _evolution_logout_from_request_body(request)


@app.get("/admin/whatsapp/evo-logout")
def admin_whatsapp_evo_logout_get(
    request: Request, token: str = Query(default=""), instance: str = Query(default="")
) -> JSONResponse:
    return _evolution_logout_apply_token(token, request, instance)


@app.post("/admin/whatsapp/evo-logout")
async def admin_whatsapp_evo_logout_post(request: Request) -> JSONResponse:
    return await _evolution_logout_from_request_body(request)


@app.get("/status")
def status(request: Request):
    """
    Health check habitual (nginx suele dejar pasar GET /status aunque filtre otras rutas).

    Logout de Evolution (mismo cuerpo que /walogout) por query, para entornos donde
    POST a /admin/... o /walogout devuelve 404 desde el proxy:
      GET /status?evolution_logout=1&token=AGENT_SECRET
    """
    q = request.query_params
    evo_flag = (q.get("evolution_logout") or q.get("evo_logout") or "").strip().lower()
    tok = (q.get("token") or "").strip()
    if evo_flag in ("1", "true", "yes"):
        if not tok:
            return JSONResponse(
                {"ok": False, "detail": "Falta token en la URL (?token=AGENT_SECRET)"},
                status_code=400,
            )
        inst_q = (q.get("instance") or "").strip()
        body_s: dict = {"token": tok}
        if inst_q:
            body_s["instance"] = inst_q
        raw = json.dumps(body_s).encode("utf-8")
        code, payload = handle_logout_post_body(raw, dict(request.headers))
        if isinstance(payload, dict):
            payload["evolution_logout_applied"] = True
        return JSONResponse(content=payload, status_code=code)
    return {
        "ok": True,
        "backend": BACKEND_NAME,
        "gemini": bool(GEMINI_API_KEY),
        "claude": bool(CLAUDE_API_KEY),
        "evolution_url": EVOLUTION_API_URL or "(no configurado)",
        "instance": EVOLUTION_INSTANCE,
        "allowed_numbers": len(WHATSAPP_ALLOWED) if WHATSAPP_ALLOWED else "⚠ ninguno definido",
        "active_sessions": len([p for p, exp in _sessions.items() if datetime.now() < exp]),
        "whatsapp_transcribe_local_first": WHATSAPP_TRANSCRIBE_LOCAL_FIRST,
        "openai_configured": bool(OPENAI_API_KEY),
        # Si falta, el proceso en el VPS es viejo: el panel de logout devolverá 404 en todas las rutas.
        "bridge_features": {
            "evolution_logout_routes": True,
            "evolution_qr_admin": True,
            "debug_events_post": True,
            "evolution_instances_post": True,
            "short_routes_j": True,
            "admin_inbox_panel": True,
            "admin_server_panel": True,
        },
        "whatsapp_debug_log": str(LOG_FILE),
        "whatsapp_debug_log_enabled": debug_log_enabled(),
    }


def _admin_token_from_request(request: Request) -> str:
    """Bearer, X-Admin-Token o ?token= (útil para <img src> en el panel inbox)."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    hdr = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token") or ""
    if hdr.strip():
        return hdr.strip()
    return (request.query_params.get("token") or "").strip()


def _require_admin(request: Request) -> None:
    """
    Protege el panel admin con token simple (Bearer).
    """
    token = _admin_token_from_request(request)
    if not token or token != ADMIN_PANEL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def _set_env_kv(key: str, value: str) -> None:
    """
    Actualiza una key puntual en .env (sin tocar el resto).
    """
    env_path = BASE_DIR / ".env"
    text = env_path.read_text(encoding="utf-8", errors="ignore")
    key = str(key).strip()
    new_line = f"{key}={value}"

    updated = False
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            out_lines.append(new_line)
            updated = True
        else:
            out_lines.append(line)

    if not updated:
        out_lines.append(new_line)

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _validate_phone_digits(s: str) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    return d


def _tail_text_lines(path: Path, limit: int = 2000) -> list[str]:
    """
    Devuelve las últimas `limit` líneas de un archivo de texto (streaming, sin cargar todo).
    """
    if not path.exists():
        return []

    # Lógica tipo "tail -n" leyendo desde el final.
    # Nota: funciona bien para logs de tamaño moderado/grande.
    limit = max(1, int(limit or 1))
    buf_size = 4096
    lines: list[str] = []
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        if pos == 0:
            return []
        carry = b""
        while pos > 0 and len(lines) < limit:
            read_size = min(buf_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            data = chunk + carry
            parts = data.splitlines()
            # Guardamos la última parte como carry si no termina en newline
            carry = b""
            if data and not data.endswith(b"\n") and parts:
                carry = parts.pop()
            for p in reversed(parts):
                try:
                    s = p.decode("utf-8", errors="ignore")
                except Exception:
                    s = str(p)
                lines.append(s)
                if len(lines) >= limit:
                    break
        if carry:
            try:
                s = carry.decode("utf-8", errors="ignore")
            except Exception:
                s = str(carry)
            # carry suele ser parcial; igual lo agregamos al principio si aplica
            lines.append(s)

    # invertimos porque fuimos agregando desde el final hacia atrás
    return list(reversed(lines))


@app.post("/admin/whatsapp/api/evolution-instances")
async def admin_whatsapp_api_evolution_instances(request: Request) -> JSONResponse:
    """
    Lista instancias en Evolution (nombre, estado). Misma clave ``AGENT_SECRET``.
    Sirve para ver qué sesión está «open» y si coincide con ``EVOLUTION_INSTANCE`` del .env.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("token") or "").strip()
    if not AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "AGENT_SECRET no configurado en el servidor"},
            status_code=503,
        )
    if token != AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "Clave incorrecta (usá el mismo valor que AGENT_SECRET)"},
            status_code=403,
        )
    out = evolution_instances_for_panel()
    return JSONResponse(content=out, status_code=200 if out.get("ok") else 502)


@app.post("/admin/whatsapp/api/debug-events")
async def admin_whatsapp_api_debug_events(request: Request) -> JSONResponse:
    """
    Últimos eventos de diagnóstico (HTTP, Evolution, logout) en memoria.
    Misma clave que AGENT_SECRET (no hace falta SSH ni abrir archivos).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    token = str(body.get("token") or "").strip()
    try:
        limit = int(body.get("limit") or 80)
    except (TypeError, ValueError):
        limit = 80
    if not AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "AGENT_SECRET no configurado en el servidor"},
            status_code=503,
        )
    if token != AGENT_SECRET:
        return JSONResponse(
            {"ok": False, "detail": "Clave incorrecta (usá el mismo valor que AGENT_SECRET)"},
            status_code=403,
        )
    events = get_recent_debug_events(limit)
    return JSONResponse({"ok": True, "events": events, "count": len(events)})


@app.get("/admin/whatsapp/api/evolution-qr")
def admin_whatsapp_api_evolution_qr() -> dict:
    """
    JSON del QR Evolution (misma respuesta que ``/evolution/api/qr``).
    Sin Bearer: el panel HTML público puede refrescar el QR; el logout sigue pidiendo token en POST.
    """
    return build_api_qr_response()


@app.post("/admin/whatsapp/api/evolution-logout")
@app.post("/admin/whatsapp/api/evolution_logout")
@app.post("/admin/whatsapp/api/evolution/logout")
async def admin_whatsapp_api_evolution_logout(request: Request) -> JSONResponse:
    raw = await request.body()
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


@app.get("/admin/whatsapp/api/evolution-logout")
async def admin_whatsapp_api_evolution_logout_get(
    request: Request,
    token: str = Query(default="", description="Misma clave que AGENT_SECRET"),
    instance: str = Query(default="", description="Instancia Evolution (opcional)"),
) -> JSONResponse:
    """
    Mismo efecto que el POST, por si un proxy bloquea POST con JSON.
    Ojo: el token queda en la URL (historial, logs del proxy); preferí POST cuando funcione.
    """
    body_l: dict = {"token": token}
    if (instance or "").strip():
        body_l["instance"] = instance.strip()
    raw = json.dumps(body_l).encode("utf-8")
    code, payload = handle_logout_post_body(raw, dict(request.headers))
    return JSONResponse(content=payload, status_code=code)


def _wa_api_dispatch(
    op: str,
    *,
    token: str,
    instance: str,
    limit: int,
    headers: dict[str, str],
) -> JSONResponse:
    """
    API embebida en ``/admin/whatsapp`` (query o POST) para cuando un proxy solo deja pasar
    esa ruta y devuelve 404 en ``/admin/whatsapp/api/*``.
    """
    op = (op or "").strip().lower()
    hdrs = headers
    if op in ("ping", "j-ping", "bridge-ping"):
        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-whatsapp-bridge",
                "via": "wa_same_url_fallback",
                "evolution_instance_env": EVOLUTION_INSTANCE,
                "evolution_api_configured": bool(EVOLUTION_API_URL and EVOLUTION_API_KEY),
            }
        )
    if op == "evolution-qr":
        return JSONResponse(build_api_qr_response())
    if op == "evolution-logout":
        body_l: dict = {"token": token.strip()}
        if (instance or "").strip():
            body_l["instance"] = instance.strip()
        raw = json.dumps(body_l).encode("utf-8")
        code, payload = handle_logout_post_body(raw, hdrs)
        return JSONResponse(content=payload, status_code=code)
    if op == "debug-events":
        if not AGENT_SECRET:
            return JSONResponse(
                {"ok": False, "detail": "AGENT_SECRET no configurado en el servidor"},
                status_code=503,
            )
        if token.strip() != AGENT_SECRET:
            return JSONResponse(
                {"ok": False, "detail": "Clave incorrecta (AGENT_SECRET)"},
                status_code=403,
            )
        lim = max(1, min(int(limit or 80), 500))
        events = get_recent_debug_events(lim)
        return JSONResponse({"ok": True, "events": events, "count": len(events)})
    if op == "evolution-instances":
        if not AGENT_SECRET:
            return JSONResponse({"ok": False, "detail": "AGENT_SECRET no configurado"}, status_code=503)
        if token.strip() != AGENT_SECRET:
            return JSONResponse(
                {"ok": False, "detail": "Clave incorrecta (AGENT_SECRET)"},
                status_code=403,
            )
        out = evolution_instances_for_panel()
        return JSONResponse(content=out, status_code=200 if out.get("ok") else 502)
    return JSONResponse({"ok": False, "detail": f"wa_op desconocido: {op}"}, status_code=400)


def _try_wa_query_api(request: Request) -> JSONResponse | None:
    q = request.query_params
    if (q.get("wa_json") or "").strip().lower() not in ("1", "true", "yes"):
        return None
    op = (q.get("wa_op") or "").strip()
    token = (q.get("wa_token") or q.get("token") or "").strip()
    instance = (q.get("wa_instance") or q.get("instance") or "").strip()
    try:
        limit = int(q.get("limit") or "80")
    except (TypeError, ValueError):
        limit = 80
    hdrs = {str(k): str(v) for k, v in request.headers.items()}
    return _wa_api_dispatch(op, token=token, instance=instance, limit=limit, headers=hdrs)


@app.post("/admin/whatsapp")
async def admin_whatsapp_wa_op_post(request: Request) -> JSONResponse:
    """
    Misma API que ``wa_json``+``wa_op`` en GET, pero por POST (token no va en la URL).
    Cuerpo: ``{"wa_op":"evolution-logout","token":"...","instance":"opcional"}``
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "detail": "JSON inválido"}, status_code=400)
    if not isinstance(body, dict) or not (body.get("wa_op") or "").strip():
        return JSONResponse(
            {"ok": False, "detail": 'Usá /admin/whatsapp/api/... o enviá JSON con "wa_op"'},
            status_code=404,
        )
    op = str(body.get("wa_op")).strip()
    token = str(body.get("token") or body.get("wa_token") or "").strip()
    instance = str(body.get("instance") or body.get("wa_instance") or "").strip()
    try:
        limit = int(body.get("limit") or 80)
    except (TypeError, ValueError):
        limit = 80
    hdrs = {str(k): str(v) for k, v in request.headers.items()}
    return _wa_api_dispatch(op, token=token, instance=instance, limit=limit, headers=hdrs)


@app.get("/admin/whatsapp")
def admin_whatsapp_page(request: Request):
    q_api = _try_wa_query_api(request)
    if q_api is not None:
        return q_api
    # El panel frontend se autentica desde el browser, pero igual sirve la pagina sin token.
    # Si queres proteccion a nivel server (bloqueo), agregame _require_admin aqui.
    panel_path = BASE_DIR / "admin_whatsapp_panel_v2.html"
    if panel_path.exists():
        panel_html = panel_path.read_text(encoding="utf-8", errors="ignore")
        # Inyecta URL del QR (qr_server); si no hay env, el JS del panel usa location.hostname:8099.
        inj = f"<script>window.__QR_WEB_PUBLIC_URL__ = {json.dumps(QR_WEB_PUBLIC_URL)};</script>"
        panel_html = panel_html.replace("<!-- QR_WEB_PUBLIC_URL_INJECT -->", inj, 1)
        dbg = (
            "<p class=\"text-secondary small mb-2\">Todo desde esta URL: con tu <strong>AGENT_SECRET</strong> usá el botón "
            "<strong>Ver diagnóstico</strong> (abajo) para ver actividad reciente sin entrar al servidor. "
            "Opcional en disco: <code class=\"mono\">"
            + html.escape(str(LOG_FILE))
            + "</code> si <code>WHATSAPP_DEBUG_LOG=1</code>.</p>"
        )
        panel_html = panel_html.replace("<!-- WHATSAPP_DEBUG_LOG_INJECT -->", dbg, 1)
        return HTMLResponse(panel_html)
    # fallback
    return HTMLResponse("<h3>Panel admin no disponible (archivo html no encontrado).</h3>", status_code=404)


@app.get("/admin/inbox")
def admin_inbox_page() -> HTMLResponse:
    panel_path = BASE_DIR / "admin_inbox_panel.html"
    if panel_path.exists():
        return HTMLResponse(panel_path.read_text(encoding="utf-8", errors="ignore"))
    return HTMLResponse("<h3>Panel inbox no disponible (falta admin_inbox_panel.html).</h3>", status_code=404)


@app.get("/admin/inbox/api/items")
def admin_inbox_api_items(request: Request) -> dict:
    _require_admin(request)
    items = _inbox_list_items()
    out = []
    for it in items:
        row = dict(it)
        row["mtime_iso"] = datetime.fromtimestamp(it["mtime"]).isoformat(timespec="seconds")
        out.append(row)
    return {"ok": True, "items": out}


@app.get("/admin/inbox/api/raw")
def admin_inbox_api_raw(request: Request, rel: str = "", token: str = "") -> FileResponse:
    _require_admin(request)
    p = _inbox_allowed_path(rel)
    if not p or not p.is_file():
        raise HTTPException(status_code=404, detail="archivo no encontrado")
    return FileResponse(path=str(p), filename=p.name)


@app.post("/admin/inbox/api/delete")
def admin_inbox_api_delete(request: Request, payload: dict) -> dict:
    _require_admin(request)
    rels = payload.get("rels") if isinstance(payload, dict) else None
    if not isinstance(rels, list):
        raise HTTPException(status_code=400, detail='Enviá JSON {"rels": ["notes/archivo.txt"]}')
    deleted: list[str] = []
    for rel in rels:
        srel = str(rel).replace("\\", "/").lstrip("/")
        p = _inbox_allowed_path(srel)
        if p and p.is_file():
            try:
                p.unlink()
                deleted.append(srel)
            except OSError:
                pass
    return {"ok": True, "deleted": deleted}


@app.put("/admin/inbox/api/note")
def admin_inbox_api_note_put(request: Request, payload: dict) -> dict:
    _require_admin(request)
    rel = str((payload or {}).get("rel") or "").replace("\\", "/").lstrip("/")
    content = (payload or {}).get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="falta content")
    if not rel.startswith("notes/") or not rel.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="solo se editan archivos .txt bajo notes/")
    p = _inbox_allowed_path(rel)
    if not p or not p.is_file():
        raise HTTPException(status_code=404, detail="nota no encontrada")
    p.write_text(str(content), encoding="utf-8")
    return {"ok": True}


@app.get("/admin/server")
def admin_server_page() -> HTMLResponse:
    panel_path = BASE_DIR / "admin_server_panel.html"
    if panel_path.exists():
        return HTMLResponse(panel_path.read_text(encoding="utf-8", errors="ignore"))
    return HTMLResponse("<h3>Panel servidor no disponible (falta admin_server_panel.html).</h3>", status_code=404)


@app.get("/admin/server/api/snapshot")
def admin_server_api_snapshot(
    request: Request,
    range_key: str = Query("24h", alias="range", description="24h, 72h o 7d"),
) -> dict:
    _require_admin(request)
    append_sample_if_due()
    rk = (range_key or "24h").lower().strip()
    if rk not in ("24h", "72h", "7d"):
        rk = "24h"
    return get_dashboard_payload(rk)


@app.get("/admin/whatsapp/api/config")
def admin_get_config(request: Request):
    _require_admin(request)
    _refresh_whatsapp_config_from_env()
    return {
        "allowedNumbers": sorted(list(WHATSAPP_ALLOWED)),
        "phoneMap": [{"from": k, "to": v} for k, v in WHATSAPP_PHONE_MAP.items()],
    }


@app.post("/admin/whatsapp/api/config")
def admin_update_config(request: Request, payload: dict):
    _require_admin(request)
    allowed_numbers = payload.get("allowedNumbers", []) or []
    phone_map = payload.get("phoneMap", []) or []

    allowed_clean: list[str] = []
    for x in allowed_numbers:
        d = _validate_phone_digits(x)
        if d:
            allowed_clean.append(d)
    allowed_clean = sorted(list(set(allowed_clean)))

    phone_map_clean: dict[str, str] = {}
    for item in phone_map:
        src = _validate_phone_digits(item.get("from"))
        dst = _validate_phone_digits(item.get("to"))
        if not src or not dst:
            continue
        phone_map_clean[src] = dst
        if src not in allowed_clean:
            allowed_clean.append(src)

    allowed_clean = sorted(list(set(allowed_clean)))
    phone_map_str = ",".join([f"{k}:{v}" for k, v in phone_map_clean.items()])

    _set_env_kv("WHATSAPP_ALLOWED_NUMBERS", ",".join(allowed_clean))
    _set_env_kv("WHATSAPP_PHONE_MAP", phone_map_str)

    _refresh_whatsapp_config_from_env()
    return {
        "allowedNumbers": allowed_clean,
        "phoneMap": [{"from": k, "to": v} for k, v in phone_map_clean.items()],
    }


@app.get("/admin/whatsapp/api/logs")
def admin_get_logs(request: Request, limit: int = 80):
    """
    Devuelve una vista simple de los últimos eventos de webhook/mensajes recibidos.
    """
    _require_admin(request)
    # El log está en la carpeta del bridge (se escribe con `>> whatsapp_stdout.log`).
    log_path = BASE_DIR / "whatsapp_stdout.log"

    raw_lines = _tail_text_lines(log_path, limit=max(2000, int(limit) * 25))

    # Filtramos para que sean "mensajes recibidos" y su texto
    # (webhook: eventos + logs que imprime el backend cuando procesa el texto).
    keep_markers = (
        "[webhook] upsert event='messages.upsert'",
        "[webhook] recibido mensaje",
        "[webhook] remap jid @lid",
        "Gemini) →",   # mensaje recibido del usuario
        "Claude) →",   # mensaje recibido del usuario
    )
    filtered: list[str] = []
    for l in raw_lines:
        if any(m in l for m in keep_markers):
            filtered.append(l)

    # Nos quedamos con las últimas `limit`
    limit = max(1, int(limit or 1))
    filtered = filtered[-limit:]
    return {
        "ok": True,
        "limit": limit,
        "lines": filtered,
    }


@app.post("/chat")
def chat(payload: dict):
    """
    Endpoint para N8N u otros servicios internos.
    Body: {"message": "...", "phone": "549...", "jid": "549...@s.whatsapp.net"}
    """
    message = payload.get("message", "").strip()
    phone = payload.get("phone", "unknown")
    jid = payload.get("jid", f"{phone}@s.whatsapp.net")
    if not message:
        return {"ok": False, "error": "message vacío"}
    _process_message(message, phone, jid)
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    """
    Recibe webhook de Evolution API.
    Configurar en Evolution API: WEBHOOK_URL=http://TU_IP:8766/webhook
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    event = payload.get("event", "")
    if event != "messages.upsert":
        return {"ok": True}

    data = payload.get("data", {})
    key = data.get("key", {})
    media_key_id: str = key.get("id", "").strip()

    jid: str = key.get("remoteJid", "")
    from_me = key.get("fromMe", False)
    key_id = (key.get("id") or "").strip()
    logger.info(
        "[webhook] upsert event=%r jid=%r fromMe=%s key.id=%r",
        event,
        jid,
        from_me,
        key_id,
    )

    # Ignorar grupos
    if "@g.us" in jid:
        return {"ok": True}

    # Solo procesar fromMe si es el chat propio del dueño (Mensajes guardados)
    if from_me and jid != WHATSAPP_SELF_LID:
        return {"ok": True}

    if from_me:
        message = data.get("message", {})
        # Citamos el mensaje entrante para que la respuesta quede en el mismo hilo (móvil + PC).
        reply_quote = _build_evolution_quote(key, message)
        text_check = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or ""
        ).strip()
        cutoff = datetime.now() - timedelta(seconds=10)
        _sent_texts[:] = [(j, t, ts) for j, t, ts in _sent_texts if ts > cutoff]
        if any((t == text_check and j == jid) for j, t, _ in _sent_texts):
            return {"ok": True}  # Es eco de respuesta de Jarvis, ignorar
        # Es el dueño escribiendo → usar número principal determinístico con JID correcto
        owner_phone = WHATSAPP_PRIMARY_OWNER_PHONE
        owner_jid = f"{owner_phone}@s.whatsapp.net"
        logger.info("[fromMe] mensaje del dueño: %s", (text_check or "")[:60])

        # Si no hay texto, intentar audio y transcribir.
        if not text_check:
            audio_obj = (
                message.get("audioMessage")
                or message.get("voiceMessage")
                or message.get("ptt")
                or message.get("documentMessage", {}).get("audioMessage")
            )
            if audio_obj:
                file_name = audio_obj.get("fileName") or audio_obj.get("filename") or "voice.ogg"
                # Evolution suele traer múltiples URLs; algunas apuntan a contenido encriptado (.enc).
                # Prioridad: elegir primero las que suelen ser descargables/decodificables.
                audio_url = None
                chosen_key = None
                for url_field in ("downloadUrl", "fileUrl", "mediaUrl", "url"):
                    val = audio_obj.get(url_field)
                    if val:
                        audio_url = val
                        chosen_key = url_field
                        break
                logger.info(
                    "[%s] Audio meta: file_name=%r chosen_key=%r audio_url_head=%r",
                    owner_phone,
                    file_name,
                    chosen_key,
                    (audio_url or "")[:80],
                )
                try:
                    logger.info(
                        "[%s] Audio meta keys=%s",
                        owner_phone,
                        sorted(list(audio_obj.keys())),
                    )
                except Exception:
                    pass
                context_info = audio_obj.get("contextInfo") or {}
                stanza_id = (context_info.get("stanzaId") or context_info.get("stanza_id") or "").strip()
                media_key_id_for_download = media_key_id or stanza_id
                logger.info(
                    "[%s] Media IDs: key.id=%r stanzaId=%r chosen=%r",
                    owner_phone,
                    media_key_id,
                    stanza_id,
                    media_key_id_for_download,
                )
                if audio_url or media_key_id_for_download:
                    user_key = owner_phone
                    if user_key not in _voice_busy:
                        _voice_busy.add(user_key)
                        await asyncio.to_thread(
                            lambda: _send(
                                owner_jid,
                                "🎧 Procesando audio... (un momento)",
                                reply_quote=reply_quote,
                            ),
                        )
                        try:
                            def _download_and_transcribe() -> str | None:
                                tmp_dir = BASE_DIR / "audio"
                                tmp_dir.mkdir(exist_ok=True, parents=True)
                                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                                suffix = ""
                                try:
                                    suffix = Path(file_name).suffix.lower()
                                except Exception:
                                    suffix = ""
                                if not suffix:
                                    suffix = ".ogg"
                                # Si Evolution trae un nombre tipo ".enc", lo normalizamos a ".ogg"
                                # para que el decoder/faster_whisper tenga algo válido.
                                if suffix.lower() not in (".ogg", ".oga", ".mp3", ".wav", ".m4a"):
                                    suffix = ".ogg"
                                tmp_path = tmp_dir / f"wa_voice_{owner_phone}_{ts}{suffix}"
                                # 1) Preferimos Evolution base64 (decodificable). 2) Si falla, fallback a GET directo.
                                audio_bytes = _evolution_download_media_base64(media_key_id_for_download, phone=owner_phone)
                                used_base64 = bool(audio_bytes)
                                total = 0
                                if audio_bytes:
                                    with open(tmp_path, "wb") as f:
                                        f.write(audio_bytes)
                                    total = len(audio_bytes)
                                    logger.info(
                                        "[%s] Descargado audio (fromMe) via base64 bytes=%s",
                                        owner_phone,
                                        total,
                                    )
                                elif audio_url:
                                    headers = {}
                                    if EVOLUTION_API_KEY:
                                        headers["apikey"] = EVOLUTION_API_KEY
                                    with httpx.Client(
                                        follow_redirects=True,
                                        timeout=httpx.Timeout(120.0, connect=20.0, read=120.0),
                                    ) as client:
                                        with client.stream("GET", audio_url, headers=headers) as r:
                                            r.raise_for_status()
                                            logger.info(
                                                "[%s] Audio GET headers content-type=%r content-length=%r",
                                                owner_phone,
                                                r.headers.get("content-type"),
                                                r.headers.get("content-length"),
                                            )
                                            with open(tmp_path, "wb") as f:
                                                for chunk in r.iter_bytes():
                                                    if chunk:
                                                        f.write(chunk)
                                                        total += len(chunk)
                                    logger.info(
                                        "[%s] Descargado audio (fromMe) bytes=%s url=%r",
                                        owner_phone,
                                        total,
                                        audio_url[:80],
                                    )
                                else:
                                    logger.warning("[%s] Sin audio_url ni media_key_id para bajar audio", owner_phone)
                                    return None

                                if total < 1024:
                                    logger.warning("[%s] Audio (fromMe) demasiado chico (%s bytes)", owner_phone, total)
                                    return None
                                _inbox_copy_path_to_audio_subdir(tmp_path, owner_phone, file_name)
                                effective_path = str(tmp_path)
                                # Normalizar con ffmpeg a WAV para maximizar decodificación.
                                wav_path = str(tmp_path.with_suffix(".wav"))
                                if wav_path != effective_path:
                                    ok = _ffmpeg_convert_to_wav(str(tmp_path), wav_path, owner_phone)
                                    if ok:
                                        effective_path = wav_path
                                # N8N / Whisper local / Gemini (ver `_transcribe_whatsapp_audio`).
                                return _transcribe_whatsapp_audio(
                                    effective_path,
                                    owner_phone,
                                    used_base64=used_base64,
                                )

                            transcription = await asyncio.wait_for(
                                asyncio.to_thread(_download_and_transcribe),
                                timeout=120,
                            )
                            if transcription and _looks_like_valid_transcription(transcription):
                                logger.info("[%s] Audio transcripto. head=%r", owner_phone, transcription[:120])
                                await asyncio.to_thread(
                                    lambda: _process_message(
                                        transcription,
                                        owner_phone,
                                        owner_jid,
                                        reply_quote=reply_quote,
                                    ),
                                )
                            else:
                                logger.warning(
                                    "[%s] Transcripción inválida o vacía. head=%r",
                                    owner_phone,
                                    (transcription or "")[:120],
                                )
                                await asyncio.to_thread(
                                    lambda: _send(
                                        owner_jid,
                                        "❌ No pude transcribir el audio. Probá enviarlo de nuevo (más corto o con mejor señal).",
                                        reply_quote=reply_quote,
                                    ),
                                )
                        finally:
                            _voice_busy.discard(user_key)
                    return {"ok": True}
            saved_fm = await asyncio.to_thread(
                _persist_whatsapp_inbox_media, message, media_key_id, owner_phone
            )
            if saved_fm:
                await asyncio.to_thread(
                    lambda: _send(
                        owner_jid,
                        "💾 Archivo guardado en la bandeja del servidor.\n"
                        f"📁 `{saved_fm}`\n"
                        "Ver o borrar: /admin/inbox (Bearer = ADMIN_PANEL_TOKEN).",
                        reply_quote=reply_quote,
                    ),
                )
            return {"ok": True}

        # Foto/video/archivo con leyenda: archivar en wa_inbox sin mensaje extra.
        await asyncio.to_thread(
            _persist_whatsapp_inbox_media, message, media_key_id, owner_phone
        )
        await asyncio.to_thread(
            lambda: _process_message(text_check, owner_phone, owner_jid, reply_quote=reply_quote),
        )
        return {"ok": True}

    # Mensajes de otras personas: verificar número autorizado.
    # Nota: Evolution puede enviar remoteJid con sufijo "@lid". No los ignoramos:
    # remapeamos para que Evolution acepte el envío de respuesta (jid esperado: @s.whatsapp.net).
    phone = jid.replace("@s.whatsapp.net", "").replace("@g.us", "").lstrip("+")
    phone_norm = re.sub(r"\D", "", phone)
    phone = phone_norm  # Normalizamos para sesiones/historial y whitelist

    # Alias para enviar: phone_norm (entrada) -> número real aceptado por Evolution.
    phone_send = WHATSAPP_PHONE_MAP.get(phone_norm, phone_norm)

    if "@lid" in jid:
        jid_before = jid
        jid = f"{phone_send}@s.whatsapp.net"
        logger.info("[webhook] remap jid @lid: %r -> %r (send=%r)", jid_before, jid, phone_send)
    else:
        # Si ya venía como @s.whatsapp.net pero el alias pide otro número, lo ajustamos igual.
        if phone_send != phone_norm:
            jid_before = jid
            jid = f"{phone_send}@s.whatsapp.net"
            logger.info("[webhook] remap jid alias: %r -> %r (send=%r)", jid_before, jid, phone_send)

    logger.info(
        "[webhook] recibido mensaje jid=%r fromMe=%s phone=%r norm=%r",
        jid,
        from_me,
        phone,
        phone_norm,
    )

    if WHATSAPP_ALLOWED and phone_norm not in WHATSAPP_ALLOWED:
        logger.info("Número no autorizado ignorado: phone=%r norm=%r", phone, phone_norm)
        return {"ok": True}

    message = data.get("message", {})
    reply_quote = _build_evolution_quote(key, message)
    text = (
        message.get("conversation")
        or message.get("extendedTextMessage", {}).get("text")
        or ""
    ).strip()

    # Si no hay texto, intentar audio (nota de voz) y transcribir.
    if not text:
        message = data.get("message", {}) or {}

        audio_obj = (
            message.get("audioMessage")
            or message.get("voiceMessage")
            or message.get("ptt")
            or message.get("documentMessage", {}).get("audioMessage")
        )

        if audio_obj:
            # Evolution API suele traer un url de descarga.
            file_name = audio_obj.get("fileName") or audio_obj.get("filename") or "voice.ogg"
            audio_url = None
            chosen_key = None
            for url_field in ("downloadUrl", "fileUrl", "mediaUrl", "url"):
                val = audio_obj.get(url_field)
                if val:
                    audio_url = val
                    chosen_key = url_field
                    break
            logger.info(
                "[%s] Audio meta: file_name=%r chosen_key=%r audio_url_head=%r",
                phone,
                file_name,
                chosen_key,
                (audio_url or "")[:80],
            )
            try:
                logger.info(
                    "[%s] Audio meta keys=%s",
                    phone,
                    sorted(list(audio_obj.keys())),
                )
            except Exception:
                pass

            context_info = audio_obj.get("contextInfo") or {}
            stanza_id = (context_info.get("stanzaId") or context_info.get("stanza_id") or "").strip()
            media_key_id_for_download = media_key_id or stanza_id
            logger.info(
                "[%s] Media IDs: key.id=%r stanzaId=%r chosen=%r",
                phone,
                media_key_id,
                stanza_id,
                media_key_id_for_download,
            )

            if not audio_url and not media_key_id_for_download:
                return {"ok": True}

            user_key = phone
            if user_key in _voice_busy:
                return {"ok": True}
            _voice_busy.add(user_key)
            await asyncio.to_thread(
                lambda: _send(
                    jid,
                    "🎧 Procesando audio... (un momento)",
                    reply_quote=reply_quote,
                ),
            )

            try:
                # Descargar y transcribir en thread (no bloquear).
                def _download_and_transcribe() -> str | None:
                    tmp_dir = BASE_DIR / "audio"
                    tmp_dir.mkdir(exist_ok=True, parents=True)
                    # Evitar colisiones con múltiples mensajes.
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    suffix = ""
                    try:
                        suffix = Path(file_name).suffix.lower()
                    except Exception:
                        suffix = ""
                    if not suffix:
                        suffix = ".ogg"
                    if suffix.lower() not in (".ogg", ".oga", ".mp3", ".wav", ".m4a"):
                        suffix = ".ogg"
                    tmp_path = tmp_dir / f"wa_voice_{phone}_{ts}{suffix}"
                    # 1) Preferimos Evolution base64 (decodificable). 2) Si falla, fallback a GET directo.
                    audio_bytes = _evolution_download_media_base64(media_key_id_for_download, phone=phone)
                    used_base64 = bool(audio_bytes)
                    total = 0
                    if audio_bytes:
                        with open(tmp_path, "wb") as f:
                            f.write(audio_bytes)
                        total = len(audio_bytes)
                        logger.info("[%s] Descargado audio via base64 bytes=%s", phone, total)
                    elif audio_url:
                        headers = {}
                        if EVOLUTION_API_KEY:
                            headers["apikey"] = EVOLUTION_API_KEY
                        with httpx.Client(
                            follow_redirects=True,
                            timeout=httpx.Timeout(120.0, connect=20.0, read=120.0),
                        ) as client:
                            with client.stream("GET", audio_url, headers=headers) as r:
                                r.raise_for_status()
                                logger.info(
                                    "[%s] Audio GET headers content-type=%r content-length=%r",
                                    phone,
                                    r.headers.get("content-type"),
                                    r.headers.get("content-length"),
                                )
                                with open(tmp_path, "wb") as f:
                                    for chunk in r.iter_bytes():
                                        if chunk:
                                            f.write(chunk)
                                            total += len(chunk)
                        logger.info(
                            "[%s] Descargado audio bytes=%s url=%r",
                            phone,
                            total,
                            audio_url[:80],
                        )
                    else:
                        logger.warning("[%s] Sin audio_url ni media_key_id para bajar audio", phone)
                        return None

                    if total < 1024:
                        logger.warning("[%s] Audio demasiado chico (%s bytes)", phone, total)
                        return None

                    _inbox_copy_path_to_audio_subdir(tmp_path, phone, file_name)
                    effective_path = str(tmp_path)
                    wav_path = str(tmp_path.with_suffix(".wav"))
                    if wav_path != effective_path:
                        ok = _ffmpeg_convert_to_wav(str(tmp_path), wav_path, phone)
                        if ok:
                            effective_path = wav_path

                    return _transcribe_whatsapp_audio(
                        effective_path,
                        phone,
                        used_base64=used_base64,
                    )

                transcription = await asyncio.wait_for(
                    asyncio.to_thread(_download_and_transcribe),
                    timeout=120,
                )
                if transcription and _looks_like_valid_transcription(transcription):
                    logger.info("[%s] Audio transcripto. head=%r", phone, transcription[:120])
                    await asyncio.to_thread(
                        lambda: _process_message(
                            transcription,
                            phone,
                            jid,
                            reply_quote=reply_quote,
                        ),
                    )
                else:
                    logger.warning(
                        "[%s] Transcripción inválida o vacía. head=%r",
                        phone,
                        (transcription or "")[:120],
                    )
                    await asyncio.to_thread(
                        lambda: _send(
                            jid,
                            "❌ No pude transcribir el audio. Probá enviarlo de nuevo (más corto o con mejor señal).",
                            reply_quote=reply_quote,
                        ),
                    )
            finally:
                _voice_busy.discard(user_key)
            return {"ok": True}

        saved_in = await asyncio.to_thread(
            _persist_whatsapp_inbox_media, message, media_key_id, phone
        )
        if saved_in:
            await asyncio.to_thread(
                lambda: _send(
                    jid,
                    "💾 Archivo guardado en la bandeja del servidor.\n"
                    f"📁 `{saved_in}`\n"
                    "Ver o borrar: /admin/inbox (Bearer = ADMIN_PANEL_TOKEN).",
                    reply_quote=reply_quote,
                ),
            )
        return {"ok": True}

    # Procesar en thread para no bloquear el event loop de FastAPI
    await asyncio.to_thread(_persist_whatsapp_inbox_media, message, media_key_id, phone)
    await asyncio.to_thread(
        lambda: _process_message(text, phone, jid, reply_quote=reply_quote),
    )
    return {"ok": True}


@app.on_event("startup")
async def _startup_log_routes() -> None:
    """En journalctl / log: confirma que este proceso incluye logout/QR (si no, el deploy es viejo)."""
    paths = [getattr(r, "path", "") for r in app.routes]
    has_wa = any("walogout" in str(p) for p in paths)
    has_evo_qr = any("evolution-qr" in str(p) for p in paths)
    logger.info(
        "whatsapp_bridge iniciado: rutas_totales=%s walogout=%s evolution-qr=%s",
        len(paths),
        has_wa,
        has_evo_qr,
    )
    log_event(
        "bridge_startup",
        {
            "rutas_totales": len(paths),
            "walogout_route": has_wa,
            "evolution_qr_admin_route": has_evo_qr,
            "debug_log_file": str(LOG_FILE),
            "debug_log_enabled": debug_log_enabled(),
        },
    )
