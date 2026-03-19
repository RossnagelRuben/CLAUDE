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
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

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
PROMPT_FILE = BASE_DIR / "agent_prompt.txt"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)

# Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").strip()   # ej: http://localhost:8080
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "").strip()   # apikey de Evolution
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "jarvis").strip()

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
WHATSAPP_PRIMARY_OWNER_PHONE = (
    WHATSAPP_ALLOWED_ORDERED[0] if WHATSAPP_ALLOWED_ORDERED else "owner"
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
    global WHATSAPP_ALLOWED, WHATSAPP_PHONE_MAP

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
# Textos recién enviados por Jarvis — para no procesar el eco del fromMe propio
_sent_texts: list[tuple[str, datetime]] = []
_voice_busy: set[str] = set()


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


def _evolution_download_media_base64(media_key_id: str, *, phone: str) -> bytes | None:
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
        if len(audio_bytes) < 1024:
            logger.warning("[%s] Audio (base64) demasiado chico: %s bytes", phone, len(audio_bytes))
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


def _ask_ai(text: str, phone: str) -> str:
    """
    Backend único de IA para WhatsApp.
    Prioridad: Gemini si está configurado, si no Claude (para no romper instalaciones previas).
    """
    if _has_gemini_text:
        return _ask_gemini(text, phone)
    return _ask_claude(text, phone)


def _send(jid: str, text: str) -> None:
    """Envía un mensaje de texto via Evolution API."""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        logger.warning("Evolution API no configurada; respuesta no enviada: %s", text[:80])
        return
    # Registrar para evitar procesar el eco fromMe
    _sent_texts.append((text.strip(), datetime.now()))
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    try:
        resp = httpx.post(
            url,
            json={"number": jid, "textMessage": {"text": text}},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Error enviando WhatsApp a %s: %s", jid, e)


def _parse_product_prefs_from_user_text(user_text: str) -> dict:
    """
    Interpreta preferencias de filtros para DRR desde el texto del usuario.
    Devuelve dict con claves:
      - limit (int | None)
      - include_prices (bool | None)
      - order (\"last_modified_desc\" | \"last_modified_asc\" | None)
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

    return {"limit": limit, "include_prices": include_prices, "order": order}


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


def _get_productos(
    descripcion: str = "",
    limit: int = 5,
    *,
    include_prices: bool = True,
    order: str | None = None,
) -> str:
    """Consulta la API DRR y devuelve productos formateados (con filtros)."""
    if not DRR_API_BASE_URL:
        return "(DRR no configurado)"
    try:
        from drr.api_client import DRRProductoAPIClient

        repo = DRRProductoAPIClient(DRR_API_BASE_URL, api_key=DRR_API_KEY or None, cache_ttl_seconds=25)
        # Si ordenamos localmente por fecha, necesitamos traer más de "limit" para que el
        # "top N" por fecha sea correcto (si el backend no soporta order explícito).
        fetch_limit = limit if order is None else max(limit, 50)
        productos = repo.listar(descripcion=descripcion or None, limit=fetch_limit)
        if not productos:
            return f"(Sin productos encontrados para: {descripcion or 'todos'})"

        total = len(productos)
        out_list = productos
        if order in ("last_modified_desc", "last_modified_asc"):
            out_list = _sort_products_by_last_modified(productos, order)

        lines = []
        for p in out_list[:limit]:
            linea = f"• {p.descripcion}"
            if p.codigo_barras:
                linea += f" | Cód: {p.codigo_barras}"
            if include_prices and p.precio is not None:
                linea += f" | ${p.precio:.2f}"
            lines.append(linea)

        result = "\n".join(lines)
        if total > limit:
            result += f"\n_(mostrando {limit} de {total})_"
        return result
    except Exception as e:
        return f"(Error DRR: {e})"


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


def _process_message(text: str, phone: str, jid: str) -> None:
    """
    Procesa un mensaje de texto:
    - Verifica sesión / autenticación
    - Detecta confirmación de comandos pendientes (SI/NO)
    - Llama a Claude y parsea prefijos NOTA:, ACCION:, CMD:, BUSCAR:
    """

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
            _send(jid, f"✅ Sesión iniciada. Hola, soy Jarvis. Sesión válida por {SESSION_HOURS}h.")
        else:
            _send(jid, "🔒 Ingresá la clave de acceso para usar Jarvis. Podés enviarla a pelo o como `/login TU_CLAVE`.")
        return

    # --- Confirmación de comando pendiente ---
    if phone in _pending_cmd:
        cmd = _pending_cmd.pop(phone)
        if text.strip().upper() in ("SI", "SÍ", "S", "YES", "Y"):
            logger.info("[%s] Confirmación recibida; ejecutando CMD=%r", phone, cmd)
            _send(jid, f"⚙️ Ejecutando: `{cmd}`")
            output = _run_command(cmd)
            logger.info("[%s] Ejecutado. output_len=%s output_head=%r", phone, len(output or ""), (output or "")[:120])
            _send(jid, f"✅ Resultado:\n```\n{output[:3000]}\n```")
        else:
            _send(jid, "❌ Comando cancelado.")
        return

    # --- IA de texto (Gemini preferido, luego Claude) ---
    logger.info("[%s] (%s) → %s", phone, BACKEND_NAME, text[:80])
    reply = _ask_ai(text, phone)
    logger.info("[%s] (%s) ← %s", phone, BACKEND_NAME, reply[:80])

    # --- Parseo de prefijos especiales ---

    if reply.startswith("NOTA:"):
        note = reply.replace("NOTA:", "", 1).strip()
        fname = BASE_DIR / "notes" / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fname.write_text(note, encoding="utf-8")
        _send(jid, f"📝 Nota guardada:\n{note}")
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
                _send(
                    jid,
                    "⚠️ Jarvis quiere ejecutar:\n"
                    f"Acción: {action}\n"
                    f"CMD: `{cmd}`\n\n"
                    "Respondé *SI* para confirmar o cualquier otra cosa para cancelar.",
                )
            else:
                _send(
                    jid,
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

        logger.info(
            "[%s] DRR filtros (from user audio/text): desc=%r limit=%s include_prices=%s order=%r",
            phone,
            desc,
            final_limit,
            final_include_prices,
            final_order,
        )

        _send(jid, f"📦 Buscando productos: {desc or 'todos'} (limit={final_limit})...")
        resultado = _get_productos(
            descripcion=desc,
            limit=final_limit,
            include_prices=final_include_prices,
            order=final_order,
        )
        _send(jid, f"📦 Productos DRR:\n{resultado}")
        return

    if reply.startswith("BUSCAR:"):
        query = reply.replace("BUSCAR:", "", 1).strip()
        _send(jid, f"🔍 Buscando: {query}...")
        results = _search_web(query)
        prompt = (
            f"Resultados de búsqueda para '{query}':\n\n{results}\n\n"
            "Resumí o respondé en español según esta información."
        )
        final = _ask_ai(prompt, phone)
        _send(jid, final[:4000])
        return

    # Respuesta normal
    _send(jid, reply[:4000])


# =========================
# ENDPOINTS
# =========================

@app.get("/status")
def status():
    return {
        "ok": True,
        "backend": BACKEND_NAME,
        "gemini": bool(GEMINI_API_KEY),
        "claude": bool(CLAUDE_API_KEY),
        "evolution_url": EVOLUTION_API_URL or "(no configurado)",
        "instance": EVOLUTION_INSTANCE,
        "allowed_numbers": len(WHATSAPP_ALLOWED) if WHATSAPP_ALLOWED else "⚠ ninguno definido",
        "active_sessions": len([p for p, exp in _sessions.items() if datetime.now() < exp]),
    }


def _require_admin(request: Request) -> None:
    """
    Protege el panel admin con token simple (Bearer).
    """
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        # fallback: por si el cliente usa X-Admin-Token
        token = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token") or ""

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


@app.get("/admin/whatsapp")
def admin_whatsapp_page(request: Request):
    # El panel frontend se autentica desde el browser, pero igual sirve la pagina sin token.
    # Si queres proteccion a nivel server (bloqueo), agregame _require_admin aqui.
    panel_path = BASE_DIR / "admin_whatsapp_panel_v2.html"
    if panel_path.exists():
        return HTMLResponse(panel_path.read_text(encoding="utf-8", errors="ignore"))
    # fallback
    return HTMLResponse("<h3>Panel admin no disponible (archivo html no encontrado).</h3>", status_code=404)


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
        text_check = (
            message.get("conversation")
            or message.get("extendedTextMessage", {}).get("text")
            or ""
        ).strip()
        cutoff = datetime.now() - timedelta(seconds=10)
        _sent_texts[:] = [(t, ts) for t, ts in _sent_texts if ts > cutoff]
        if any(t == text_check for t, _ in _sent_texts):
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
                for key in ("downloadUrl", "fileUrl", "mediaUrl", "url"):
                    val = audio_obj.get(key)
                    if val:
                        audio_url = val
                        chosen_key = key
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
                            _send,
                            owner_jid,
                            "🎧 Procesando audio... (un momento)",
                        )
                        try:
                            def _download_and_transcribe() -> str | None:
                                import transcribe_core
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
                                effective_path = str(tmp_path)
                                # Normalizar con ffmpeg a WAV para maximizar decodificación.
                                wav_path = str(tmp_path.with_suffix(".wav"))
                                if wav_path != effective_path:
                                    ok = _ffmpeg_convert_to_wav(str(tmp_path), wav_path, owner_phone)
                                    if ok:
                                        effective_path = wav_path
                                # 1) Si está configurado TRANSCRIBE_API_URL (N8N o API 8765), usarlo.
                                api_txt = _transcribe_audio_via_api(effective_path, owner_phone)
                                if api_txt and api_txt.strip() and api_txt.strip() != "(sin voz detectada)":
                                    return api_txt

                                # 2) Fallback local.
                                try:
                                    return transcribe_core.transcribe_voice(effective_path)
                                except Exception as e:
                                    logger.exception("[%s] Error transcribiendo audio (fromMe): %s", owner_phone, e)
                                    # Si solo pudimos bajar el `.enc` (no base64 decodificable), evitamos que Gemini "invente".
                                    if not used_base64:
                                        return None
                                    gem = _gemini_transcribe_audio(effective_path)
                                    if gem:
                                        return gem
                                    return None

                            transcription = await asyncio.wait_for(
                                asyncio.to_thread(_download_and_transcribe),
                                timeout=120,
                            )
                            if transcription and _looks_like_valid_transcription(transcription):
                                logger.info("[%s] Audio transcripto. head=%r", owner_phone, transcription[:120])
                                await asyncio.to_thread(_process_message, transcription, owner_phone, owner_jid)
                            else:
                                logger.warning(
                                    "[%s] Transcripción inválida o vacía. head=%r",
                                    owner_phone,
                                    (transcription or "")[:120],
                                )
                                await asyncio.to_thread(
                                    _send,
                                    owner_jid,
                                    "❌ No pude transcribir el audio. Probá enviarlo de nuevo (más corto o con mejor señal).",
                                )
                        finally:
                            _voice_busy.discard(user_key)
            return {"ok": True}

        await asyncio.to_thread(_process_message, text_check, owner_phone, owner_jid)
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
            for key in ("downloadUrl", "fileUrl", "mediaUrl", "url"):
                val = audio_obj.get(key)
                if val:
                    audio_url = val
                    chosen_key = key
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
                _send,
                jid,
                "🎧 Procesando audio... (un momento)",
            )

            try:
                # Descargar y transcribir en thread (no bloquear).
                def _download_and_transcribe() -> str | None:
                    import transcribe_core

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

                    effective_path = str(tmp_path)
                    wav_path = str(tmp_path.with_suffix(".wav"))
                    if wav_path != effective_path:
                        ok = _ffmpeg_convert_to_wav(str(tmp_path), wav_path, phone)
                        if ok:
                            effective_path = wav_path

                    # 1) Si está configurado TRANSCRIBE_API_URL (N8N o API 8765), usarlo.
                    api_txt = _transcribe_audio_via_api(effective_path, phone)
                    if api_txt and api_txt.strip() and api_txt.strip() != "(sin voz detectada)":
                        return api_txt

                    # 2) Fallback local.
                    try:
                        return transcribe_core.transcribe_voice(effective_path)
                    except Exception as e:
                        logger.exception("[%s] Error transcribiendo audio: %s", phone, e)
                        # Si solo pudimos bajar el `.enc` (no base64), evitamos Gemini "inventando".
                        if not used_base64:
                            return None
                        gem = _gemini_transcribe_audio(effective_path)
                        if gem:
                            return gem
                        return None

                transcription = await asyncio.wait_for(
                    asyncio.to_thread(_download_and_transcribe),
                    timeout=120,
                )
                if transcription and _looks_like_valid_transcription(transcription):
                    logger.info("[%s] Audio transcripto. head=%r", phone, transcription[:120])
                    await asyncio.to_thread(_process_message, transcription, phone, jid)
                else:
                    logger.warning(
                        "[%s] Transcripción inválida o vacía. head=%r",
                        phone,
                        (transcription or "")[:120],
                    )
                    await asyncio.to_thread(
                        _send,
                        jid,
                        "❌ No pude transcribir el audio. Probá enviarlo de nuevo (más corto o con mejor señal).",
                    )
            finally:
                _voice_busy.discard(user_key)
        return {"ok": True}

    # Procesar en thread para no bloquear el event loop de FastAPI
    await asyncio.to_thread(_process_message, text, phone, jid)
    return {"ok": True}
