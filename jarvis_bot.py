"""
Jarvis Bot — Asistente de servidor con memoria, logs, voz y Nextcloud.

- Texto y voz: responde con Gemini; los mensajes de voz se transcriben con transcripción local o API.
- Memoria: todo se registra en logs diarios (Markdown). Opcional NEXTCLOUD_DIR para sincronizar.
- Comandos: /start, /login, /log, /resumen, /proyecto, etc. El menú / se configura al iniciar.
"""

import asyncio
import atexit
import base64
import logging
import os
import random
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timedelta
from typing import Optional
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (
    CallbackQueryHandler,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

# Log dedicado DRR (listar, callbacks, errores) para revisar fallos sin mezclar con log general
from drr.chat_intents import parse_edit_image_intent, parse_producto_imagen_index
from drr.logger import drr_log
from jarvis_datetime_context import format_datetime_context_for_system_prompt
from jarvis_prompt import compose_agent_system_prompt
from jarvis_text_display import strip_markdown_display_symbols
import google_workspace

# =========================
# CONFIG — Variables de entorno desde .env (no subir .env a repos)
# =========================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()
AGENT_SECRET = os.getenv("AGENT_SECRET", "").strip()
# Imágenes: Gemini (Imagen) y/o OpenAI (DALL·E 3)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# DRR APIs: productos (opcional). Ver docs/DOCUMENTACION_MIGRACION_DRR.md
DRR_API_BASE_URL = os.getenv("DRR_API_BASE_URL", "").strip()
DRR_API_KEY = os.getenv("DRR_API_KEY", "").strip()
# Auth DRR (cliente → dev → usuario → final). NO hardcodear; cargar desde .env.
DRR_CLIENT_TOKEN = os.getenv("DRR_CLIENT_TOKEN", "").strip()
DRR_DEV_USER = os.getenv("DRR_DEV_USER", "").strip()
DRR_DEV_PASSWORD = os.getenv("DRR_DEV_PASSWORD", "").strip()
# Paths auth (opcionales; alineados al Swagger público de DRR)
DRR_AUTH_DEV_PATH = os.getenv("DRR_AUTH_DEV_PATH", "/Auth/TokenDeveloper").strip() or "/Auth/TokenDeveloper"
DRR_AUTH_USER_PATH = os.getenv("DRR_AUTH_USER_PATH", "/Auth/TokenUser").strip() or "/Auth/TokenUser"
# OpenClaw: backend de IA. Si está activo, se usa en lugar de Claude.
# Requiere openclaw-sdk (pip install -r requirements-openclaw.txt) y una instancia OpenClaw corriendo.
# USE_OPENCLAW=1 → usa gateway local (ws://127.0.0.1:18789/gateway) por defecto.
OPENCLAW_USE = os.getenv("USE_OPENCLAW", "").strip() in ("1", "true", "yes")
OPENCLAW_GATEWAY_WS_URL = os.getenv("OPENCLAW_GATEWAY_WS_URL", "").strip()
OPENCLAW_OPENAI_BASE_URL = os.getenv("OPENCLAW_OPENAI_BASE_URL", "").strip()
# Gateway local por defecto cuando USE_OPENCLAW=1 y no se define OPENCLAW_GATEWAY_WS_URL
OPENCLAW_DEFAULT_WS = "ws://127.0.0.1:18789/gateway"
OPENCLAW_AGENT_ID = os.getenv("OPENCLAW_AGENT_ID", "jarvis").strip() or "jarvis"

PROMPT_FILE = BASE_DIR / "agent_prompt.txt"
NOTES_DIR = BASE_DIR / "notes"
# Logs: si NEXTCLOUD_DIR está definido, se guarda ahí (sincroniza con Nextcloud)
LOG_DIR = Path(os.getenv("NEXTCLOUD_DIR", "").strip() or str(BASE_DIR / "logs"))
PROJECTS_DIR = LOG_DIR / "proyectos"
# Carpeta temporal para descargar notas de voz (Telegram envía .ogg); se limpia al transcribir
VOICE_TEMP_DIR = BASE_DIR / "voice_tmp"
PRODUCTOS_IMAGENES_DIR = BASE_DIR / "productos_imagenes"
NOTES_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
VOICE_TEMP_DIR.mkdir(exist_ok=True)
PRODUCTOS_IMAGENES_DIR.mkdir(exist_ok=True)

# Transcripción: si TRANSCRIBE_API_URL está definida, el bot envía el audio ahí (más rápido, no bloquea).
# Si no, usa transcribe_core en proceso (con lock interno).
TRANSCRIBE_API_URL = os.getenv("TRANSCRIBE_API_URL", "").strip()
# Usuarios que están en medio de procesar un audio (evita que un segundo audio cuelgue todo)
_voice_busy: set[int] = set()
# Clave en context.user_data para "respuesta de voz con Gemini Live" (opción "cambiar a gemini")
USE_GEMINI_VOICE_KEY = "use_gemini_voice"
# Modelo Gemini Live para voz nativa (entrada/salida audio)
GEMINI_LIVE_VOICE_MODEL = os.getenv("GEMINI_LIVE_VOICE_MODEL", "gemini-2.5-flash-native-audio-preview-09-2025").strip() or "gemini-2.5-flash-native-audio-preview-09-2025"

# Log de auditoría: registro en tiempo real para diagnóstico y automejora (errores, timeouts, duraciones).
AUDIT_LOG_DIR = BASE_DIR / "agent_data" / "logs"
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "jarvis_audit.log"
AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _audit_log(event: str, detail: str = "", **kwargs) -> None:
    """
    Escribe una línea en jarvis_audit.log y en el logger para registro en tiempo real.
    Formato: [ISO] event | detail key=value ...
    Eventos de voz: voice_start, voice_api_ok, voice_api_fail, voice_api_timeout,
    voice_local_fallback, voice_timeout, voice_error. Sirve para diagnóstico y automejora.
    """
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
    line = f"[{ts}] {event} | {detail}".strip()
    if extra:
        line += f" {extra}"
    line += "\n"
    try:
        with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("No se pudo escribir en audit log: %s", e)
    logger.info("[audit] %s | %s %s", event, detail, extra)

if not TELEGRAM_TOKEN:
    raise RuntimeError("Falta TELEGRAM_TOKEN en .env")
_has_openclaw = bool(OPENCLAW_USE or OPENCLAW_GATEWAY_WS_URL or OPENCLAW_OPENAI_BASE_URL)
# Backend de texto: preferimos Gemini si está configurado, y OpenClaw como respaldo opcional.
_has_gemini_text = bool(GEMINI_API_KEY)
if not _has_openclaw and not _has_gemini_text:
    raise RuntimeError(
        "Falta un backend de IA de texto: "
        "configurá GEMINI_API_KEY o (opcional) OpenClaw (OPENCLAW_GATEWAY_WS_URL / OPENCLAW_OPENAI_BASE_URL) en .env"
    )
if not ALLOWED_CHAT_ID:
    raise RuntimeError("Falta ALLOWED_CHAT_ID en .env")
if not AGENT_SECRET:
    raise RuntimeError("Falta AGENT_SECRET en .env")
if not PROMPT_FILE.exists():
    raise RuntimeError("Falta agent_prompt.txt")

# Nombre legible del backend principal de texto (para logs y /start).
BACKEND_NAME = "Gemini" if _has_gemini_text else "OpenClaw"

auth_until = None
pending_command = None
pending_code = None

# Sesión guardada en archivo para que todos los procesos (y tras reinicio) la vean
SESSION_FILE = BASE_DIR / ".jarvis_session"
# Candado: solo una instancia del bot puede correr (evita 409 Conflict con Telegram)
LOCK_FILE = BASE_DIR / ".jarvis_bot.lock"

def _load_session() -> datetime | None:
    """Lee la fecha de vencimiento de la sesión desde el archivo."""
    if not SESSION_FILE.exists():
        return None
    try:
        s = SESSION_FILE.read_text(encoding="utf-8").strip()
        dt = datetime.fromisoformat(s)
        return dt if datetime.now() < dt else None
    except Exception:
        return None

def _save_session(expiry: datetime) -> None:
    """Guarda la fecha de vencimiento de la sesión en archivo."""
    SESSION_FILE.write_text(expiry.isoformat(), encoding="utf-8")

# Al arrancar, cargar sesión si existe (para que este proceso también la vea)
auth_until = _load_session()

def _get_auth_until() -> datetime | None:
    """Devuelve la fecha de vencimiento de sesión (memoria o archivo)."""
    global auth_until
    if auth_until is not None and datetime.now() < auth_until:
        return auth_until
    auth_until = _load_session()
    return auth_until

def is_authorized(update: Update) -> bool:
    return str(update.effective_chat.id) == ALLOWED_CHAT_ID

def session_ok() -> bool:
    return _get_auth_until() is not None

def session_remaining() -> str:
    until = _get_auth_until()
    if until is None:
        return "sin sesión"
    delta = until - datetime.now()
    if delta.total_seconds() <= 0:
        return "vencida"
    return str(delta).split(".")[0]

def _search_web_sync(query: str, max_results: int = 6) -> str:
    """Búsqueda en internet con DuckDuckGo (sync). Devuelve texto con título y snippet por resultado."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "(No se encontraron resultados)"
        lines = []
        for i, r in enumerate(results, 1):
            title = (r.get("title") or "").strip()
            body = (r.get("body") or "").strip()
            if title or body:
                lines.append(f"{i}. {title}\n   {body[:300]}")
        return "\n\n".join(lines) if lines else "(Sin contenido)"
    except Exception as e:
        logger.warning("Error en búsqueda web: %s", e)
        return f"(Error al buscar: {e})"


async def search_web(query: str, max_results: int = 6) -> str:
    """Búsqueda en internet sin bloquear el event loop."""
    return await asyncio.to_thread(_search_web_sync, query.strip(), max_results)


def ask_claude(user_text: str, context_memory: str | None = None) -> str:
    """
    Claude fue deshabilitado: el bot ahora usa Gemini como backend de texto.
    Se deja este stub solo por compatibilidad (no se debería llamar).
    """
    return "(Claude deshabilitado. Usá GEMINI_API_KEY y el backend de Gemini.)"


def ask_gemini(user_text: str, context_memory: str | None = None) -> str:
    """
    Envía el mensaje del usuario a Gemini (texto) con opcional contexto de logs recientes.
    Se usa como backend principal de Jarvis cuando hay GEMINI_API_KEY configurada.
    """
    if not GEMINI_API_KEY:
        return "(Gemini no configurado. Configurá GEMINI_API_KEY en .env.)"
    try:
        from google import genai
    except ImportError as e:
        return f"(Gemini no disponible, falta dependencia google-genai: {e})"

    system_prompt = compose_agent_system_prompt(BASE_DIR) + format_datetime_context_for_system_prompt()
    if context_memory:
        system_prompt += "\n\n--- CONTEXTO RECIENTE (logs de días anteriores) ---\n" + context_memory

    user_message = user_text
    if context_memory:
        user_message = (
            "[El usuario tiene acceso a logs diarios. Usá NOTA: para apuntes y proponé comandos con ACCION:/CMD: "
            "cuando haga falta. Respetá siempre el formato de acciones ya definido para Jarvis.]\n\n"
            + user_message
        )

    client_gemini = genai.Client(api_key=GEMINI_API_KEY)
    # Algunos modelos históricos pueden devolver 404 para nuevas cuentas.
    # Probamos el modelo configurado y, si falla, intentamos alternativas.
    primary = os.getenv("GEMINI_TEXT_MODEL", "").strip() or "gemini-1.5-flash"
    fallbacks = [
        primary,
        "gemini-1.5-flash-002",
        "gemini-1.5-pro",
        "gemini-2.5-flash",
    ]

    last_error = None
    for model_name in fallbacks:
        try:
            response = client_gemini.models.generate_content(
                model=model_name,
                contents=[{"role": "user", "parts": [{"text": user_message}]}],
                config={"system_instruction": system_prompt},
            )
            # La API de Gemini puede devolver varias partes; tomamos el texto agregado.
            text = (response.text or "").strip()
            if text:
                return text
            return "(Gemini no devolvió texto)"
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            if "404" in msg or "not_found" in msg or "no longer available" in msg:
                continue
            continue

    err = str(last_error)[:500] if last_error else "error desconocido"
    return f"(Error Gemini: {err})"


def _openclaw_configured() -> bool:
    """True si OpenClaw está activo (USE_OPENCLAW=1 o gateway/URL definidos)."""
    return bool(OPENCLAW_USE or OPENCLAW_GATEWAY_WS_URL or OPENCLAW_OPENAI_BASE_URL)


async def get_ai_response(user_text: str, context_memory: str | None = None) -> str:
    """
    Obtiene la respuesta de IA para texto.
    Prioridad:
      1) Gemini si hay GEMINI_API_KEY.
      2) OpenClaw si está configurado.
    Async para no bloquear cuando se usa OpenClaw (SDK async) o backends síncronos.
    """
    if _has_gemini_text:
        return await asyncio.to_thread(ask_gemini, user_text, context_memory)

    if _openclaw_configured():
        try:
            from openclaw_sdk import OpenClawClient

            # Construir mensaje con contexto para compatibilidad con despliegues previos.
            user_message = user_text
            tz_ctx = format_datetime_context_for_system_prompt().strip()
            if context_memory:
                user_message = (
                    tz_ctx
                    + "\n\n[El usuario tiene acceso a logs diarios. Usá NOTA: para apuntes y proponé comandos con ACCION:/CMD: cuando haga falta.]\n\n"
                    "--- CONTEXTO RECIENTE ---\n"
                    + context_memory
                    + "\n\n--- MENSAJE ---\n"
                    + user_text
                )
            else:
                user_message = tz_ctx + "\n\n--- MENSAJE ---\n" + user_text
            # Conexión: URL explícita o auto-detect (gateway local 127.0.0.1:18789)
            kwargs = {}
            if OPENCLAW_GATEWAY_WS_URL:
                kwargs["gateway_ws_url"] = OPENCLAW_GATEWAY_WS_URL
            elif OPENCLAW_OPENAI_BASE_URL:
                kwargs["openai_base_url"] = OPENCLAW_OPENAI_BASE_URL
            elif OPENCLAW_USE:
                kwargs["gateway_ws_url"] = OPENCLAW_DEFAULT_WS
            async with OpenClawClient.connect(**kwargs) as oc_client:
                agent = oc_client.get_agent(OPENCLAW_AGENT_ID)
                result = await agent.execute(user_message)
            if result.success and result.content:
                return result.content.strip()
            return "(OpenClaw no devolvió texto)"
        except ImportError:
            logger.warning(
                "OpenClaw está activo pero openclaw-sdk no está instalado. "
                "Ejecutá: pip install -r requirements-openclaw.txt."
            )
        except Exception as e:
            logger.warning("Error al usar OpenClaw (%s): %s", type(e).__name__, e)

    return "(No hay backend de IA de texto disponible: configurá GEMINI_API_KEY o (opcional) OpenClaw.)"


def run_command(command: str) -> str:
    """Ejecuta un comando en el servidor (timeout 120s); usado tras /confirm y /estado."""
    from server_executor import default_executor
    r = default_executor.run(command, timeout_seconds=120)
    return r.output

def save_note(text: str) -> str:
    filename = NOTES_DIR / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filename.write_text(text.strip(), encoding="utf-8")
    return str(filename)


# =========================
# GEMINI LIVE — Voz nativa (opción "cambiar a gemini")
# =========================

def _ogg_to_pcm_16k(audio_bytes: bytes | None = None, audio_path: str | Path | None = None) -> bytes | None:
    """Convierte audio OGG/Opus a PCM 16-bit 16 kHz mono (formato que pide Gemini Live). Usa ffmpeg."""
    import shutil
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg no encontrado; no se puede usar Gemini Live para voz.")
        return None
    try:
        if audio_path is not None:
            inp = str(audio_path)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", inp, "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
                capture_output=True,
                timeout=60,
                check=False,
            )
        else:
            if not audio_bytes:
                return None
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", "pipe:0", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
                input=audio_bytes,
                capture_output=True,
                timeout=60,
                check=False,
            )
        if proc.returncode != 0 or not proc.stdout:
            logger.warning("ffmpeg ogg->pcm falló: %s", (proc.stderr or b"")[:300].decode(errors="replace"))
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.warning("Error convirtiendo ogg a PCM: %s", e)
        return None


def _pcm_24k_to_ogg(pcm_bytes: bytes) -> bytes | None:
    """Convierte PCM 24 kHz 16-bit mono a OGG Opus (para enviar como mensaje de voz en Telegram)."""
    import shutil
    if not shutil.which("ffmpeg") or not pcm_bytes:
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "-"],
            input=pcm_bytes,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            logger.warning("ffmpeg pcm->ogg falló: %s", (proc.stderr or b"")[:300].decode(errors="replace"))
            return None
        return proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.warning("Error convirtiendo PCM a ogg: %s", e)
        return None


async def _gemini_live_voice_response(audio_pcm_16k: bytes, system_instruction: str) -> bytes | None:
    """
    Envía el audio PCM 16 kHz a Gemini Live API y devuelve el audio de respuesta (PCM 24 kHz).
    Requiere GEMINI_API_KEY y google-genai con client.aio.live.
    """
    if not GEMINI_API_KEY or not audio_pcm_16k:
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        logger.warning("google.genai no disponible para Live API: %s", e)
        return None
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        system_text = (
            "Sos Jarvis, asistente de voz del servidor. Respondé siempre en español, breve y claro. "
            "Si el usuario pide hacer algo en el servidor, decile que puede escribirlo por texto y confirmar con /confirm."
        )
        if system_instruction:
            system_text = system_instruction
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": types.Content(role="user", parts=[types.Part(text=system_text)]),
        }
        chunks_collected: list[bytes] = []
        async with client.aio.live.connect(model=GEMINI_LIVE_VOICE_MODEL, config=config) as session:
            # Enviar audio en un solo blob (o en trozos si es muy largo: ~32k bytes = 1 s)
            chunk_size = 32000
            for i in range(0, len(audio_pcm_16k), chunk_size):
                chunk = audio_pcm_16k[i : i + chunk_size]
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
            await session.send_realtime_input(audio_stream_end=True)
            async for message in session.receive():
                if message.data:
                    chunks_collected.append(message.data)
        if not chunks_collected:
            return None
        return b"".join(chunks_collected)
    except Exception as e:
        logger.exception("Error en Gemini Live voz: %s", e)
        return None


# =========================
# MEMORIA Y LOGS (Nextcloud-ready)
# =========================

def _log_path(date: datetime) -> Path:
    return LOG_DIR / f"{date.strftime('%Y-%m-%d')}.md"


def append_log(role: str, content: str, entry_type: str = "message") -> None:
    """Escribe en el log del día (Markdown). role: user | assistant | sistema. entry_type: message | voz | nota | comando | error."""
    path = _log_path(datetime.now())
    ts = datetime.now().strftime("%H:%M:%S")
    # Escapar bloques de código para no romper el MD
    safe = content.replace("```", "` ` `").strip()
    if not safe:
        return
    block = f"\n### {ts} — {role}" + (f" ({entry_type})" if entry_type != "message" else "") + "\n\n"
    block += safe[:15000] + ("…" if len(content) > 15000 else "") + "\n"
    if not path.exists():
        path.write_text(f"# Log {path.stem}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def get_log_for_date(date: datetime) -> str:
    """Return full content of the log for a given date, or empty string."""
    path = _log_path(date)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def list_log_dates() -> list[str]:
    """Return sorted list of available log dates (YYYY-MM-DD)."""
    dates = []
    for p in LOG_DIR.glob("*.md"):
        if p.name != "README.md" and len(p.stem) == 10:
            dates.append(p.stem)
    return sorted(dates, reverse=True)


def get_recent_logs_for_context(days: int = 3, max_chars: int = 3500) -> str:
    """Get recent logs content for Gemini/OpenClaw context, truncated to max_chars."""
    parts = []
    total = 0
    today = datetime.now().date()
    for i in range(days):
        d = today - timedelta(days=i)
        content = get_log_for_date(datetime.combine(d, datetime.min.time()))
        if not content:
            continue
        part = f"--- {d} ---\n{content[:max_chars // days]}"
        if len(content) > max_chars // days:
            part += "\n..."
        parts.append(part)
        total += len(part)
        if total >= max_chars:
            break
    return "\n\n".join(parts) if parts else ""


def create_project(name: str, description: str) -> str:
    """Create a project file in LOG_DIR/proyectos and return path."""
    safe_name = "".join(c for c in name if c.isalnum() or c in " -_").strip()[:80] or "proyecto"
    safe_name = safe_name.replace(" ", "_")
    path = PROJECTS_DIR / f"{safe_name}.md"
    header = f"# {name}\n\nCreado: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    path.write_text(header + "## Descripción\n\n" + description.strip() + "\n\n## Notas\n\n", encoding="utf-8")
    return str(path)


# =========================
# VOZ — Transcripción: API externa (recomendado) o transcribe_core en proceso
# Timeouts y fallback evitan que un servidor lento o N8N caído cuelgue al usuario.
# =========================

# Timeout HTTP para la llamada a API/N8N (algo menor que API_TIMEOUT del handler para fallback limpio).
TRANSCRIBE_HTTP_TIMEOUT = 40


def _transcribe_via_api(audio_bytes: bytes, filename: str) -> tuple[str | None, float]:
    """
    Envía el audio a TRANSCRIBE_API_URL (multipart) y devuelve (texto o None, duración_seg).
    None si falla o timeout; el caller puede hacer fallback a transcripción local.
    """
    if not TRANSCRIBE_API_URL:
        return None, 0.0
    import json as _json
    import time as _time
    start = _time.perf_counter()
    # Si es webhook de N8N, la URL ya es la final; si es API directa, añadir /transcribe
    base = TRANSCRIBE_API_URL.rstrip("/")
    url = base if "/webhook/" in base else f"{base}/transcribe"
    boundary = b"----JarvisTranscribe" + base64.b64encode(os.urandom(8)).rstrip(b"=").replace(b"/", b"_")
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="audio"; filename="' + filename.encode() + b'"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + audio_bytes + b"\r\n"
        b"--" + boundary + b"--\r\n"
    )
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary.decode())
        with urllib.request.urlopen(req, timeout=TRANSCRIBE_HTTP_TIMEOUT) as resp:
            data = _json.loads(resp.read().decode())
            text = data.get("text") or None
        duration = _time.perf_counter() - start
        if text:
            _audit_log("voice_api_ok", "", duration_sec=round(duration, 2), bytes=len(audio_bytes))
        return text, duration
    except Exception as e:
        duration = _time.perf_counter() - start
        _audit_log("voice_api_fail", str(e)[:200], duration_sec=round(duration, 2), bytes=len(audio_bytes))
        logger.warning("Transcripción vía API falló (%.1fs): %s", duration, e)
        return None, duration


def _transcribe_local(audio_path: str) -> str:
    """Transcripción en proceso (transcribe_core). Usada como fallback si API falla o timeout."""
    from transcribe_core import transcribe_voice
    return transcribe_voice(audio_path)

# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        logger.warning("Chat no autorizado. Recibido id=%s (ALLOWED_CHAT_ID=%s)", update.effective_chat.id, ALLOWED_CHAT_ID)
        await update.message.reply_text("⛔ chat no autorizado")
        return

    await update.message.reply_text(
        f"🤖 Jarvis activo ({BACKEND_NAME}). Podés pedirme cambios en el servidor: ejecutar comandos, crear/editar archivos, instalar, reiniciar servicios, etc. Confirmo con /confirm.\n\n"
        "Comandos:\n"
        "/login TU_CLAVE · /authstatus · /estado\n"
        "/confirm CODIGO · /cancel\n\n"
        "Memoria y logs:\n"
        "/log [YYYY-MM-DD] · /dias · /resumen [N]\n"
        "/proyecto Nombre | Descripción\n\n"
        "Generar:\n"
        "/audio <texto> · /imagen <descripción> · /editarimagen <instrucción>\n"
        "/buscar <consulta>\n\n"
        "Voz: /cambiargemini (alternar respuesta de voz con Gemini Live o modo normal)\n\n"
        "Productos (DRR): /productos"
    )

async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global auth_until

    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return

    if not context.args:
        await update.message.reply_text("Uso: /login TU_CLAVE")
        return

    if context.args[0].strip() != AGENT_SECRET:
        await update.message.reply_text("❌ clave incorrecta")
        return

    global auth_until
    auth_until = datetime.now() + timedelta(hours=24)
    _save_session(auth_until)  # así otros procesos o tras reinicio siguen reconociendo la sesión
    await update.message.reply_text(
        f"✅ autenticado por 24h\n"
        f"vence: {auth_until.strftime('%Y-%m-%d %H:%M:%S')}"
    )

async def authstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return

    if session_ok():
        await update.message.reply_text(f"🔓 sesión activa\nrestante: {session_remaining()}")
    else:
        await update.message.reply_text("🔒 sesión cerrada o vencida")

async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return

    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return

    try:
        cmd = "uptime && echo && free -h && echo && df -h /"
        result = run_command(cmd)
        await update.message.reply_text("📄 Resultado bruto de /estado:\n\n" + result)
        # Enviar también a la IA para que lo explique como un profesor.
        context_memory = get_recent_logs_for_context(days=1, max_chars=2000)
        explicacion_prompt = (
            "Acabo de ejecutar este comando en el servidor:\n\n"
            f"{cmd}\n\n"
            "Y esta fue la salida (resultado):\n\n"
            f"{result[:2000]}\n\n"
            "Explicá en español claro qué significa este resultado, como si fueras un profesor, "
            "y qué conclusiones puedo sacar sobre el estado del servidor. "
            "No propongas nuevos comandos ni uses ACCION:, CMD:, NOTA:, IMAGEN:, AUDIO:, BUSCAR: ni BUSCAR_IMAGEN:. "
            "Solo una explicación y, si corresponde, recomendaciones de alto nivel."
        )
        try:
            explicacion = await get_ai_response(explicacion_prompt, context_memory=context_memory)
            await update.message.reply_text("🧠 Explicación de /estado:\n\n" + explicacion[:4000])
        except Exception as e2:
            logger.warning("Error pidiendo explicación IA para /estado: %s", e2)
    except Exception as e:
        await update.message.reply_text(f"❌ error en /estado: {e}")

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pending_command, pending_code

    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return

    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return

    if pending_command is None:
        await update.message.reply_text("No hay ninguna acción pendiente.")
        return

    if not context.args:
        await update.message.reply_text("Uso: /confirm CODIGO")
        return

    code = context.args[0].strip()

    if code != pending_code:
        await update.message.reply_text("❌ código incorrecto")
        return

    cmd = pending_command
    pending_command = None
    pending_code = None

    await update.message.reply_text(f"✅ ejecutando:\n{cmd}")

    try:
        from server_executor import default_executor
        r = default_executor.run(cmd, timeout_seconds=120)
        append_log("sistema", f"Comando ejecutado: {cmd}\nResultado:\n{r.output[:2000]}", entry_type="comando")
        # Primero, mostrar el resultado bruto para transparencia.
        reply = "📄 Resultado del comando:\n\n" + r.output
        if not r.success and r.hint:
            reply += "\n\n💡 " + r.hint
        await update.message.reply_text(reply[:4000])

        # Luego, pedir a la IA que lo explique como un profesor y proponga aprendizajes / próximos pasos.
        try:
            contexto = get_recent_logs_for_context(days=1, max_chars=2000)
            explicacion_prompt = (
                "Acabo de ejecutar este comando en el servidor:\n\n"
                f"{cmd}\n\n"
                "Y esta fue la salida (resultado):\n\n"
                f"{r.output[:2000]}\n\n"
                "Explicá en español claro qué significa este resultado, como si fueras un profesor, "
                "qué conclusiones puedo sacar y qué próximos pasos tendría sentido hacer. "
                "NO propongas comandos con ACCION: ni CMD:, ni uses NOTA:, IMAGEN:, AUDIO:, BUSCAR: ni BUSCAR_IMAGEN:. "
                "Solo una explicación y recomendaciones de alto nivel para aprender."
            )
            explicacion = await get_ai_response(explicacion_prompt, context_memory=contexto)
            await update.message.reply_text("🧠 Explicación del comando:\n\n" + explicacion[:4000])
        except Exception as e2:
            logger.warning("Error pidiendo explicación IA para comando '%s': %s", cmd, e2)
    except Exception as e:
        append_log("sistema", f"Error ejecutando {cmd}: {e}", entry_type="error")
        await update.message.reply_text(f"❌ error ejecutando comando: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pending_command, pending_code

    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return

    pending_command = None
    pending_code = None
    await update.message.reply_text("✅ acción pendiente cancelada")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ver log del día: /log [YYYY-MM-DD]. Sin argumentos = hoy."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    try:
        if context.args:
            day_str = context.args[0].strip()
            day = datetime.strptime(day_str, "%Y-%m-%d")
        else:
            day = datetime.now()
        content = get_log_for_date(day)
        if not content:
            await update.message.reply_text(f"📋 No hay log para {day.strftime('%Y-%m-%d')}.")
            return
        # Telegram limita ~4096 caracteres por mensaje
        if len(content) > 4000:
            await update.message.reply_text(f"📋 Log {day.strftime('%Y-%m-%d')} (inicio):\n\n" + content[:3900] + "\n\n…")
            await update.message.reply_text("… " + content[3900:7800] if len(content) > 3900 else "")
        else:
            await update.message.reply_text(f"📋 Log {day.strftime('%Y-%m-%d')}:\n\n" + content)
    except ValueError:
        await update.message.reply_text("Uso: /log [YYYY-MM-DD] (ej: /log 2026-03-14)")


async def cmd_dias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Listar fechas con log disponible: /dias"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    dates = list_log_dates()
    if not dates:
        await update.message.reply_text("📅 Aún no hay logs. Escribí algo y se creará el de hoy.")
        return
    msg = "📅 Días con log:\n\n" + "\n".join(dates[:30])
    if len(dates) > 30:
        msg += f"\n… y {len(dates) - 30} más"
    await update.message.reply_text(msg)


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resumen de los últimos N días: /resumen [N]. Sin argumentos = 1 día."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    try:
        n = 1
        if context.args:
            n = max(1, min(7, int(context.args[0].strip())))
    except ValueError:
        n = 1
    await update.message.reply_text("📝 Generando resumen...")
    try:
        raw = get_recent_logs_for_context(days=n, max_chars=6000)
        if not raw:
            await update.message.reply_text("No hay logs recientes para resumir.")
            return
        prompt = (
            f"Resumí en español, de forma clara y breve, lo que pasó en los últimos {n} día(s) "
            "según este log: temas tratados, notas guardadas, comandos ejecutados, decisiones o pendientes. "
            "Usá viñetas y no más de 15 líneas."
        )
        summary = await get_ai_response(prompt + "\n\n--- LOG ---\n" + raw, context_memory=None)
        await update.message.reply_text("📝 Resumen:\n\n" + summary[:4000])
    except Exception as e:
        logger.exception("Error en /resumen: %s", e)
        await update.message.reply_text(f"❌ Error generando resumen: {e}")


async def cmd_proyecto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Crear proyecto: /proyecto Nombre del proyecto | Descripción breve."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    if not context.args:
        await update.message.reply_text("Uso: /proyecto Nombre | Descripción\nEj: /proyecto API Rest | Backend en FastAPI")
        return
    full = " ".join(context.args).strip()
    if "|" in full:
        name, desc = full.split("|", 1)
        name, desc = name.strip(), desc.strip()
    else:
        name, desc = full, "Sin descripción"
    if not name:
        await update.message.reply_text("El nombre del proyecto no puede estar vacío.")
        return
    try:
        path = create_project(name, desc)
        append_log("sistema", f"Proyecto creado: {name}\n{path}", entry_type="proyecto")
        await update.message.reply_text(f"✅ Proyecto creado:\n{path}\n\n{name}\n{desc[:200]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# =========================
# AUDIO (TTS) — edge_tts, sin API key
# =========================

async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Genera audio a partir de texto (TTS con edge_tts). Uso: /audio texto a pronunciar"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Uso: /audio <texto a convertir en voz>\nEj: /audio Hola, soy Jarvis")
        return
    if len(text) > 2000:
        await update.message.reply_text("El texto no puede superar 2000 caracteres.")
        return
    await _do_generate_audio(update, context, text)


# =========================
# IMAGEN — OpenAI DALL·E 3 (requiere OPENAI_API_KEY en .env)
# =========================

async def cmd_editarimagen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edita la última imagen (foto enviada o generada) con Gemini según el prompt. Uso: /editarimagen instrucción"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "Uso: /editarimagen instrucción\n"
            "Ej: /editarimagen cambia el fondo a una playa al atardecer\n"
            "Primero enviá una foto o generá una imagen con /imagen."
        )
        return
    await _do_edit_image(update, context, prompt)


async def cmd_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Genera una imagen con Gemini (Imagen) o OpenAI (DALL·E 3). Uso: /imagen descripción"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    if not GEMINI_API_KEY and not OPENAI_API_KEY:
        await update.message.reply_text(
            "Configurá GEMINI_API_KEY o OPENAI_API_KEY en el .env del servidor.\n"
            "Gemini: https://aistudio.google.com/apikey | OpenAI: https://platform.openai.com/api-keys"
        )
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("Uso: /imagen <descripción>\nEj: /imagen Un gato astronauta en Marte")
        return
    if len(prompt) > 1000:
        await update.message.reply_text("La descripción no puede superar 1000 caracteres.")
        return
    await _do_generate_image(update, context, prompt)


async def cmd_cripto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Jarvis Cripto: precios CMC, top, historial, balance simulado, cotización Jupiter (sin custodia de claves)."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    from crypto.commands import try_handle_crypto_command

    args = context.args or []
    line = "/cripto " + " ".join(args) if args else "/cripto"
    uid = str(update.effective_chat.id)
    out = try_handle_crypto_command(line, uid, _get_crypto_service())
    if out:
        await _reply_crypto_message(update, out, uid)


# =========================
# BÚSQUEDA EN INTERNET (DuckDuckGo, sin API key)
# =========================

async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca en internet y responde usando el backend de texto (Gemini). Uso: /buscar qué querés buscar"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("Uso: /buscar <consulta>\nEj: /buscar clima Buenos Aires mañana")
        return
    await update.message.reply_text("🔍 Buscando en internet...")
    try:
        results_text = await search_web(query)
        prompt = (
            f"El usuario buscó: {query}\n\n"
            "Información encontrada en internet:\n"
            f"{results_text}\n\n"
            "Resumí o respondé en español según esta información. Si no hay nada relevante, decilo brevemente."
        )
        respuesta = await get_ai_response(prompt, context_memory=None)
        append_log("sistema", f"Búsqueda /buscar: {query[:60]}...", entry_type="busqueda")
        await update.message.reply_text(respuesta[:4000])
    except Exception as e:
        logger.exception("Error /buscar: %s", e)
        await update.message.reply_text(f"❌ Error al buscar: {e}")


async def cmd_cambiargemini(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alterna entre voz con Gemini Live y el flujo normal (transcripción + respuesta con Gemini + TTS)."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    ud = context.user_data or {}
    prev = ud.get(USE_GEMINI_VOICE_KEY, False)
    ud[USE_GEMINI_VOICE_KEY] = not prev
    if ud[USE_GEMINI_VOICE_KEY]:
        if not GEMINI_API_KEY:
            ud[USE_GEMINI_VOICE_KEY] = False
            await update.message.reply_text(
                "❌ No está configurada GEMINI_API_KEY. Añadila al .env para usar voz con Gemini."
            )
            return
        await update.message.reply_text(
            "✅ Cambiaste a Gemini para la voz.\n\n"
            "A partir de ahora, cuando envíes un mensaje de voz, te responderé con la voz de Gemini (Live API). "
            "Para volver al modo normal, escribí /cambiargemini de nuevo."
        )
    else:
        await update.message.reply_text(
            "✅ Volviste al modo normal (transcripción + Gemini + TTS).\n\n"
            "Los mensajes de voz se transcriben y Gemini responde; si pedís audio, se genera con TTS. "
            "Para usar de nuevo la voz de Gemini, escribí /cambiargemini o «cambiar a Gemini»."
        )


# =========================
# DRR PRODUCTOS — Consulta API, búsqueda de imágenes (DuckDuckGo), guardar/mejorar
# Ver docs/DOCUMENTACION_MIGRACION_DRR.md
# =========================

def _get_servicio_productos():
    """Construye el servicio DRR solo si está configurada la URL (evita fallos si no se usa)."""
    if not DRR_API_BASE_URL:
        return None
    from drr.api_client import DRRProductoAPIClient
    from drr.auth import DRRTokenProvider, TokenConfig
    from drr.image_search import DuckDuckGoBuscadorImagenes
    from drr.storage import AlmacenImagenesLocal
    from drr.service import ServicioProductos

    # Por defecto:
    # - si hay DRR_API_KEY, se usa directo.
    # - si NO hay DRR_API_KEY pero sí credenciales DRR, se intenta flujo cliente→dev→usuario→final.
    token_provider = None
    if not DRR_API_KEY and DRR_CLIENT_TOKEN and DRR_DEV_USER and DRR_DEV_PASSWORD:
        cfg = TokenConfig(
            base_url=DRR_API_BASE_URL,
            token_cliente=DRR_CLIENT_TOKEN,
            usuario=DRR_DEV_USER,
            password=DRR_DEV_PASSWORD,
            path_token_dev=DRR_AUTH_DEV_PATH,
            path_token_usuario=DRR_AUTH_USER_PATH,
        )
        token_provider = DRRTokenProvider(cfg)

    repo = DRRProductoAPIClient(
        DRR_API_BASE_URL,
        api_key=DRR_API_KEY or None,
        token_provider=token_provider,
        cache_ttl_seconds=25,
    )
    buscador = DuckDuckGoBuscadorImagenes()
    almacen = AlmacenImagenesLocal(PRODUCTOS_IMAGENES_DIR)
    return ServicioProductos(repo, buscador, almacen)


_crypto_service = None


def _get_crypto_service():
    global _crypto_service
    if _crypto_service is None:
        from crypto import build_crypto_service

        _crypto_service = build_crypto_service(BASE_DIR)
    return _crypto_service


async def _reply_crypto_message(update: Update, reply_text: str, user_id: str) -> None:
    """Envía respuesta cripto; si hay swap recién armado, adjunta el base64 completo."""
    svc = _get_crypto_service()
    chunk = 4096
    plain = strip_markdown_display_symbols(reply_text)
    for i in range(0, len(plain), chunk):
        await update.message.reply_text(plain[i : i + chunk])
    prep = svc.peek_last_prepared(user_id)
    if prep and "🧾 Swap preparado" in reply_text:
        await update.message.reply_document(
            document=BytesIO(prep.swap_transaction_base64.encode("ascii")),
            filename="jupiter_swap_tx.b64.txt",
            caption="TX base64 completa para firmar en Phantom / Solflare.",
        )


def _build_drr_zero_log(servicio, descripcion: str | None, codigo_barras: str | None) -> str:
    """Arma el log detallado cuando listar devuelve 0 productos (para copiar y depurar)."""
    lines = [
        "--- DRR búsqueda sin resultados ---",
        f"Filtros: descripcion={repr(descripcion)} codigo_barras={repr(codigo_barras)}",
    ]
    repo = getattr(servicio, "repo", None)
    if repo and getattr(repo, "last_request_info", None):
        info = repo.last_request_info()
        lines.append(f"URL: {info.get('url', '')}")
        lines.append(f"Response bytes: {info.get('response_bytes', 0)}")
        lines.append(f"From cache: {info.get('from_cache', False)}")
    lines.append(f"Timestamp: {datetime.now().isoformat()}")
    return "\n".join(lines)


def _parse_product_prefs_from_user_text(user_text: str) -> dict:
    """
    Interpreta preferencias de filtros para DRR desde el texto del usuario (Telegram o transcripción).

    Devuelve dict con claves:
      - limit (int | None)
      - include_prices (bool | None)
      - order ("last_modified_desc" | "last_modified_asc" | None)
      - solo_lista_precio_id (int | None)
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
    Intenta extraer una fecha de última modificación desde campos extra del Producto (DRR).
    """
    if not isinstance(extra, dict):
        return None

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
    solo_lista_precio_id: int | None = None,
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

        from drr.formatter import linea_producto_resumen
        from drr.lista_precios import nombres_listas_precio

        nombres: dict[int, str] = {}
        if include_prices:
            nombres = nombres_listas_precio(DRR_API_BASE_URL, DRR_API_KEY or None)

        lines = [
            linea_producto_resumen(
                p,
                include_prices=include_prices,
                nombres_lista_precio=nombres if include_prices else None,
                solo_lista_precio_id=solo_lista_precio_id,
            )
            for p in out_list[:limit]
        ]

        result = "\n\n".join(lines)
        if total > limit:
            result += f"\n\n_(mostrando {limit} de {total})_"
        return result
    except Exception as e:
        return f"(Error DRR: {e})"


def _download_image_bytes(url: str) -> bytes | None:
    """Descarga una imagen desde URL (User-Agent de navegador)."""
    try:
        from drr.web_image_search import download_image_url

        return download_image_url(url, max_bytes=20 * 1024 * 1024)
    except Exception as e:
        logger.warning("Error descargando imagen %s: %s", url[:50], e)
        return None


def _search_web_image_and_download(query: str, max_size: int = 20 * 1024 * 1024) -> tuple[bytes | None, str]:
    """
    DuckDuckGo (reintentos / regiones) + descarga con httpx.
    Si no hay resultados web, respaldo con imagen generada (GEMINI / OpenAI).
    """
    from drr.image_generate_env import generate_image_bytes_env
    from drr.web_image_search import search_web_image_bytes

    img_bytes, mime = search_web_image_bytes(query, max_size=max_size)
    if img_bytes:
        return img_bytes, mime
    if os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip():
        logger.info(
            "BUSCAR_IMAGEN: sin resultados web para %r; usando imagen generada como respaldo",
            (query or "")[:80],
        )
        gen = generate_image_bytes_env(
            f"Fotografía o ilustración realista, un solo encuadre claro, tema: {query}"
        )
        if gen:
            return gen, "image/png"
    return None, ""


async def cmd_productos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Menú DRR: listar, ver, buscar imagen, guardar/mejorar imagen.
    Uso: /productos [ayuda|listar|ver|imagen ...]
    """
    if not is_authorized(update):
        await update.message.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await update.message.reply_text("🔒 primero /login TU_CLAVE")
        return

    servicio = _get_servicio_productos()
    args = (context.args or [])[:20]
    sub = (args[0] or "").lower() if args else "ayuda"

    # Ayuda
    if sub == "ayuda" or sub == "help":
        await update.message.reply_text(
            "📦 DRR Productos\n\n"
            "• /productos listar descripcion <texto> — filtrar por descripción\n"
            "• /productos listar codigo <código> — filtrar por código de barras\n"
            "• /productos listar todo — últimos productos\n"
            "• /productos listar sin_imagen | con_imagen | con_codigo | sin_codigo — filtros (combinables)\n"
            "• /productos ver <id o código> — detalle y imagen actual\n"
            "• /productos imagen buscar <código o descripción> — busca imagen (DuckDuckGo)\n"
            "• /productos imagen guardar <id o código> [índice] — guarda la última imagen buscada\n"
            "• /productos imagen mejorar <id o código> [descripción] — genera/mejora imagen con IA\n"
            "• /productos imagen ver <id o código> — ver imagen guardada localmente"
        )
        return

    if not servicio:
        await update.message.reply_text(
            "❌ DRR no configurado. Añadí DRR_API_BASE_URL en el .env del servidor.\n"
            "Ver docs/DOCUMENTACION_MIGRACION_DRR.md"
        )
        return

    try:
        # Atajo: permitir `/productos coca cola` como búsqueda por descripción
        if sub not in ("ayuda", "help", "listar", "ver", "imagen"):
            query = " ".join(args).strip()
            if not query:
                await update.message.reply_text("Uso: /productos listar descripcion <texto>\nEj: /productos listar descripcion leche")
                return
            await update.message.reply_text("📋 Buscando productos...")
            text, lista = await asyncio.to_thread(servicio.listar, descripcion=query, codigo_barras=None, limit=10)
            append_log(
                "sistema",
                f"DRR /Producto (atajo): Search='{query}' -> {len(lista)} resultado(s)",
                entry_type="producto",
            )
            if len(lista) == 0:
                drr_log("listar_atajo", f"descripcion={query!r} -> 0 resultados")
                log_para_copiar = _build_drr_zero_log(servicio, descripcion=query, codigo_barras=None)
                context.user_data["drr_last_zero_log"] = log_para_copiar
                keyboard = InlineKeyboardMarkup.from_button(
                    InlineKeyboardButton("📋 Log para copiar", callback_data="drr_log_copy")
                )
                await update.message.reply_text(
                    text[:4000],
                    reply_markup=keyboard,
                )
            else:
                drr_log("listar_atajo", f"descripcion={query!r} -> {len(lista)} productos, botones 1..{len(lista)}")
                context.user_data["drr_last_lista"] = [
                    {"id": p.id, "codigo_barras": p.codigo_barras or "", "descripcion": p.descripcion or ""}
                    for p in lista
                ]
                botones = [
                    InlineKeyboardButton(f"🖼 {i + 1}", callback_data=f"drr_buscar_{i}")
                    for i in range(len(lista))
                ]
                keyboard = InlineKeyboardMarkup.from_row(botones)
                await update.message.reply_text(text[:4000], reply_markup=keyboard)
            return

        # Listar
        if sub == "listar":
            listar_args = args[1:] if len(args) > 1 else []
            tipo = (listar_args[0] or "").lower() if listar_args else ""
            filter_keys = ("sin_imagen", "con_imagen", "con_codigo", "sin_codigo")
            filters_set = {w.lower() for w in listar_args if w.lower() in filter_keys}
            # Resto: tokens después del primero, excluyendo filtros (para descripcion/codigo)
            rest_tokens = [w for w in listar_args[1:] if w.lower() not in filter_keys]
            if tipo in filter_keys:
                filters_set.add(tipo)
            resto = " ".join(rest_tokens).strip() if rest_tokens else ""
            descripcion = resto if tipo == "descripcion" else None
            codigo = resto if tipo == "codigo" else None
            if tipo == "todo" or tipo in filter_keys:
                if tipo != "descripcion" and tipo != "codigo":
                    descripcion = None
                    codigo = None
            con_imagen = True if "con_imagen" in filters_set else (False if "sin_imagen" in filters_set else None)
            con_codigo_barra = True if "con_codigo" in filters_set else (False if "sin_codigo" in filters_set else None)
            await update.message.reply_text("📋 Buscando productos...")
            text, lista = await asyncio.to_thread(
                servicio.listar,
                descripcion=descripcion,
                codigo_barras=codigo,
                limit=10,
                con_imagen=con_imagen,
                con_codigo_barra=con_codigo_barra,
            )
            append_log(
                "sistema",
                f"DRR listar: desc='{descripcion or ''}' codigo='{codigo or ''}' con_imagen={con_imagen} con_codigo_barra={con_codigo_barra} -> {len(lista)}",
                entry_type="producto",
            )
            if len(lista) == 0:
                drr_log("listar", f"filtros desc={descripcion!r} codigo={codigo!r} con_imagen={con_imagen} con_codigo_barra={con_codigo_barra} -> 0 resultados")
                log_para_copiar = _build_drr_zero_log(servicio, descripcion=descripcion, codigo_barras=codigo)
                context.user_data["drr_last_zero_log"] = log_para_copiar
                keyboard = InlineKeyboardMarkup.from_button(
                    InlineKeyboardButton("📋 Log para copiar", callback_data="drr_log_copy")
                )
                await update.message.reply_text(
                    text[:4000],
                    reply_markup=keyboard,
                )
            else:
                drr_log("listar", f"filtros -> {len(lista)} productos, botones 1..{len(lista)}")
                context.user_data["drr_last_lista"] = [
                    {"id": p.id, "codigo_barras": p.codigo_barras or "", "descripcion": p.descripcion or ""}
                    for p in lista
                ]
                botones = [
                    InlineKeyboardButton(f"🖼 {i + 1}", callback_data=f"drr_buscar_{i}")
                    for i in range(len(lista))
                ]
                keyboard = InlineKeyboardMarkup.from_row(botones)
                await update.message.reply_text(text[:4000], reply_markup=keyboard)
            return

        # Ver detalle
        if sub == "ver":
            id_codigo = " ".join(args[1:]).strip() if len(args) > 1 else ""
            if not id_codigo:
                await update.message.reply_text("Uso: /productos ver <id o código de barras>")
                return
            text, producto = await asyncio.to_thread(servicio.ver, id_codigo)
            await update.message.reply_text(text)
            if producto and DRR_API_BASE_URL:
                from drr.api_client import fetch_product_image_bytes_for_snapshot

                snap = producto.to_snapshot()
                img_bytes = await asyncio.to_thread(
                    fetch_product_image_bytes_for_snapshot,
                    snap,
                    base_url=DRR_API_BASE_URL,
                    api_key=DRR_API_KEY or None,
                )
                if img_bytes:
                    await update.message.reply_photo(photo=BytesIO(img_bytes), caption="Imagen actual (API)")
            # Si hay imagen guardada localmente, también mostrarla
            if producto:
                local_bytes, _ = await asyncio.to_thread(servicio.ver_imagen_actual, producto.id)
                if local_bytes:
                    await update.message.reply_photo(photo=BytesIO(local_bytes), caption="Imagen guardada (local)")
            return

        # Subcomandos bajo "imagen"
        if sub == "imagen" and len(args) >= 2:
            accion = (args[1] or "").lower()
            resto_args = args[2:]

            if accion == "buscar":
                query = " ".join(resto_args).strip() if resto_args else ""
                if not query:
                    await update.message.reply_text("Uso: /productos imagen buscar <código o descripción>")
                    return
                await update.message.reply_text("🔍 Buscando imágenes...")
                resultados = await asyncio.to_thread(servicio.buscar_imagen, query, 5)
                context.user_data["drr_last_images"] = resultados
                urls = [r.get("image") or r.get("url") for r in resultados if r.get("image") or r.get("url")]
                context.user_data["drr_image_search_urls"] = urls
                context.user_data["drr_image_search_index"] = 0
                context.user_data["drr_image_search_id_codigo"] = None
                drr_log("imagen_buscar", f"query={query!r} -> {len(urls)} URLs")
                if not resultados:
                    await update.message.reply_text("No se encontraron imágenes.")
                    return
                first = resultados[0]
                url = first.get("image") or first.get("url")
                if url:
                    img_bytes = await asyncio.to_thread(_download_image_bytes, url)
                    if img_bytes:
                        nav_buttons = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton("◀ Anterior", callback_data="drr_img_prev"),
                                InlineKeyboardButton("Siguiente ▶", callback_data="drr_img_next"),
                            ]
                        ])
                        caption = f"1/{len(resultados)} — Para guardar: /productos imagen guardar <id o código> [índice]"
                        await update.message.reply_photo(
                            photo=BytesIO(img_bytes),
                            caption=caption,
                            reply_markup=nav_buttons,
                        )
                    else:
                        await update.message.reply_text(f"Imagen encontrada (no se pudo descargar): {url[:80]}…")
                return

            if accion == "guardar":
                id_codigo = resto_args[0].strip() if resto_args else ""
                indice = 0
                if len(resto_args) >= 2 and resto_args[1].isdigit():
                    indice = int(resto_args[1])
                if not id_codigo:
                    await update.message.reply_text("Uso: /productos imagen guardar <id o código> [índice]")
                    return
                ultimas = context.user_data.get("drr_last_images") or []
                if indice >= len(ultimas):
                    await update.message.reply_text(
                        "Primero buscá una imagen con /productos imagen buscar <consulta>. "
                        "O indicá un índice válido (0-based)."
                    )
                    return
                url = ultimas[indice].get("image") or ultimas[indice].get("url")
                if not url:
                    await update.message.reply_text("Esa entrada no tiene URL de imagen.")
                    return
                # Resolver id numérico si pasaron código
                _, producto = await asyncio.to_thread(servicio.ver, id_codigo)
                id_producto = producto.id if producto else id_codigo
                path = await asyncio.to_thread(servicio.guardar_imagen_desde_url, id_producto, url)
                if path:
                    await update.message.reply_text(f"✅ Imagen guardada en:\n{path}")
                    append_log("sistema", f"DRR imagen guardada: {path}", entry_type="producto")
                else:
                    await update.message.reply_text("❌ No se pudo guardar la imagen.")
                return

            if accion == "mejorar":
                id_codigo = resto_args[0].strip() if resto_args else ""
                desc_extra = " ".join(resto_args[1:]).strip() if len(resto_args) > 1 else ""
                if not id_codigo:
                    await update.message.reply_text("Uso: /productos imagen mejorar <id o código> [descripción extra]")
                    return
                _, producto = await asyncio.to_thread(servicio.ver, id_codigo)
                if not producto:
                    await update.message.reply_text("Producto no encontrado.")
                    return
                prompt = type(servicio).prompt_para_mejorar_imagen(producto, desc_extra)
                await update.message.reply_text("🖼 Generando imagen con IA...")
                ok = await _do_generate_image(update, context, prompt)
                if ok:
                    append_log("sistema", f"DRR imagen mejorada: {producto.descripcion[:50]}...", entry_type="producto")
                return

            if accion == "ver":
                id_codigo = " ".join(resto_args).strip() if resto_args else ""
                if not id_codigo:
                    await update.message.reply_text("Uso: /productos imagen ver <id o código>")
                    return
                local_bytes, ruta = await asyncio.to_thread(servicio.ver_imagen_actual, id_codigo)
                if local_bytes and ruta:
                    await update.message.reply_photo(photo=BytesIO(local_bytes), caption=ruta)
                else:
                    await update.message.reply_text("No hay imagen guardada localmente para ese producto.")
                return

    except Exception as e:
        logger.exception("Error /productos: %s", e)
        await update.message.reply_text(f"❌ Error: {e}")


# =========================
# EDICIÓN DE IMAGEN CON GEMINI (Nanobanana / Imagen) — enviar foto o usar la última generada
# =========================

# Clave en context.user_data para la última imagen disponible para editar (foto del usuario o generada por el bot).
LAST_IMAGE_FOR_EDIT_KEY = "last_image_for_edit"
# Límite de tamaño para guardar en contexto (API Imagen acepta hasta 20 MB).
MAX_IMAGE_EDIT_BYTES = 20 * 1024 * 1024

async def _do_edit_image(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> bool:
    """
    Edita la última imagen guardada (foto del usuario o generada) con Gemini Imagen según el prompt.
    Requiere GEMINI_API_KEY. La imagen editada se envía por Telegram y se guarda como nueva 'última' para encadenar ediciones.
    """
    if not GEMINI_API_KEY:
        await update.message.reply_text("Para editar imágenes necesito GEMINI_API_KEY en el servidor (Gemini/Imagen).")
        return False
    data = (context.user_data or {}).get(LAST_IMAGE_FOR_EDIT_KEY)
    if not data or not data.get("bytes"):
        await update.message.reply_text(
            "No tengo ninguna imagen para editar. Enviá una foto o generá una con /imagen (o pedime una por chat) y después decime cómo editarla."
        )
        return False
    prompt = prompt.strip()[:1000]
    if not prompt:
        await update.message.reply_text("Escribí qué cambio querés (ej: cambia el fondo a una playa).")
        return False
    await update.message.reply_text("🖼 Editando imagen con Gemini...")
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
        if out_bytes:
            await update.message.reply_photo(photo=BytesIO(out_bytes), caption=prompt[:200])
            context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": out_bytes, "mime_type": "image/png"}
            append_log("sistema", f"Imagen editada (Gemini): {prompt[:80]}...", entry_type="imagen")
            return True
    except Exception as e:
        logger.exception("Error editando imagen con Gemini: %s", e)
        await update.message.reply_text(f"❌ No pude editar la imagen: {e}")
    return False


# =========================
# GENERACIÓN DESDE CHAT (misma lógica que /imagen y /audio, para cuando Claude responde IMAGEN: o AUDIO:)
# =========================

async def _do_generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str) -> bool:
    """Genera imagen con Gemini o OpenAI; devuelve True si se envió, False si falló."""
    if not GEMINI_API_KEY and not OPENAI_API_KEY:
        await update.message.reply_text("No hay API key de imagen configurada (GEMINI_API_KEY u OPENAI_API_KEY).")
        return False
    prompt = prompt.strip()[:1000]
    if not prompt:
        return False
    await update.message.reply_text("🖼 Generando imagen...")
    from drr.image_generate_env import generate_image_bytes_env

    image_bytes = generate_image_bytes_env(prompt)
    if image_bytes:
        append_log("sistema", f"Imagen (chat): {prompt[:80]}...", entry_type="imagen")
        await update.message.reply_photo(photo=BytesIO(image_bytes), caption=prompt[:200])
        # Guardar como última imagen para que el usuario pueda pedir editarla con Gemini (Nanobanana).
        context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": image_bytes, "mime_type": "image/png"}
        return True
    await update.message.reply_text("❌ No se pudo generar la imagen.")
    return False


async def _do_generate_audio(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Genera audio TTS con edge_tts; devuelve True si se envió."""
    text = text.strip()[:2000]
    if not text:
        return False
    await update.message.reply_text("🔊 Generando audio...")
    tmp_path = VOICE_TEMP_DIR / f"tts_{update.effective_chat.id}_{datetime.now().strftime('%H%M%S')}.mp3"
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice="es-AR-ElenaNeural")
        await communicate.save(str(tmp_path))
        with open(tmp_path, "rb") as f:
            await update.message.reply_voice(voice=f)
        append_log("sistema", f"Audio (chat): {text[:80]}...", entry_type="audio")
        return True
    except Exception as e:
        logger.exception("Error TTS: %s", e)
        await update.message.reply_text(f"❌ Error al generar audio: {e}")
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _telegram_drive_resolve_path(rel: str) -> Optional[Path]:
    """Ruta local bajo notes/ o wa_inbox/ para DRIVE_SUBIR (Telegram)."""
    rel = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    p = (BASE_DIR / rel).resolve()
    try:
        p.relative_to(BASE_DIR.resolve())
    except ValueError:
        return None
    notes = (BASE_DIR / "notes").resolve()
    inbox = (BASE_DIR / "wa_inbox").resolve()
    try:
        p.relative_to(notes)
        return p if p.is_file() else None
    except ValueError:
        pass
    try:
        p.relative_to(inbox)
        return p if p.is_file() else None
    except ValueError:
        return None


# =========================
# TEXT AND VOICE HANDLERS (lógica compartida para texto y voz transcrita)
# =========================

async def _process_user_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    from_voice: bool = False,
) -> None:
    """
    Procesa un mensaje del usuario (texto o transcripción de voz): loguea, pide contexto
    a Gemini, responde y registra todo en el log diario. NOTA/CMD se manejan igual.
    """
    global pending_command, pending_code

    if not text.strip():
        return

    # =========================
    # Jarvis Cripto (/cripto o mensaje «cripto …»)
    # =========================
    from crypto.commands import try_handle_crypto_command

    uid_msg = str(update.effective_chat.id)
    crypto_out = try_handle_crypto_command(text, uid_msg, _get_crypto_service())
    if crypto_out is not None:
        await _reply_crypto_message(update, crypto_out, uid_msg)
        append_log("user", text.strip())
        append_log("assistant", crypto_out[:1500])
        return

    # =========================
    # Confirmación Google Calendar (misma convención SI/NO que WhatsApp)
    # =========================
    pending_cal = (context.user_data or {}).get("pending_calendar_event")
    if pending_cal:
        tcal = text.strip().upper()
        if tcal in ("SI", "SÍ", "S", "YES", "Y"):
            context.user_data.pop("pending_calendar_event", None)
            try:
                pl = google_workspace.normalize_calendar_payload(pending_cal)
                ev = google_workspace.create_calendar_event(
                    pl["title"],
                    pl["start"],
                    end_iso=pl.get("end"),
                    description=pl.get("description"),
                )
                link = (ev.get("htmlLink") or "").strip()
                msg = "✅ Evento creado en Google Calendar." + (f"\n{link}" if link else "")
                await update.message.reply_text(msg)
                append_log("assistant", msg, entry_type="message")
            except Exception as e:
                logger.exception("Telegram calendar create")
                await update.message.reply_text(f"❌ No se pudo crear el evento: {e}")
            return
        if tcal in ("NO", "N", "CANCELAR"):
            context.user_data.pop("pending_calendar_event", None)
            await update.message.reply_text("Ok, no lo agendé en el calendario.")
            return
        context.user_data.pop("pending_calendar_event", None)

    # =========================
    # OPCIÓN "cambiar a gemini" / "volver a modo normal" por texto
    # =========================
    t_lower = text.strip().lower()
    if re.search(r"cambiar(s)?\s+a\s+gemini|usar\s+gemini\s+para\s+voz|gemini\s+para\s+voz", t_lower):
        context.user_data[USE_GEMINI_VOICE_KEY] = True
        if not GEMINI_API_KEY:
            context.user_data[USE_GEMINI_VOICE_KEY] = False
            await update.message.reply_text("❌ No está configurada GEMINI_API_KEY. Añadila al .env para usar voz con Gemini.")
        else:
            await update.message.reply_text("✅ Cambiaste a Gemini para la voz. Enviá un mensaje de voz y te responderé con la voz de Gemini. Para volver al modo normal: /cambiargemini.")
        append_log("user", text.strip())
        append_log("assistant", "Usuario activó voz con Gemini.")
        return
    if re.search(r"volver\s+a\s+claude|cambiar(s)?\s+a\s+claude|desactivar\s+gemini\s+voz", t_lower):
        context.user_data[USE_GEMINI_VOICE_KEY] = False
        await update.message.reply_text("✅ Volviste al modo normal (transcripción + Gemini + TTS).")
        append_log("user", text.strip())
        append_log("assistant", "Usuario volvió al modo normal para voz.")
        return

    # =========================
    # Imagen de producto DRR (listado previo), por lenguaje natural
    # =========================
    idx_drr = parse_producto_imagen_index(text.strip())
    if idx_drr is not None and (context.user_data or {}).get("drr_last_products_snap"):
        snaps = context.user_data["drr_last_products_snap"]
        if not (1 <= idx_drr <= len(snaps)):
            await update.message.reply_text(
                f"En el último listado solo hay {len(snaps)} producto(s). Pedí un número entre 1 y {len(snaps)}."
            )
            return
        row = snaps[idx_drr - 1]
        if not DRR_API_BASE_URL:
            await update.message.reply_text("❌ DRR no está configurado (DRR_API_BASE_URL).")
            return
        await update.message.reply_text("🖼 Descargando imagen del producto…")
        from drr.api_client import fetch_product_image_bytes_for_snapshot

        img_bytes = await asyncio.to_thread(
            fetch_product_image_bytes_for_snapshot,
            row,
            base_url=DRR_API_BASE_URL,
            api_key=DRR_API_KEY or None,
        )
        if img_bytes:
            desc = (row.get("descripcion") or "")[:100]
            cap = f"Producto {idx_drr}: {desc}" if desc else f"Producto {idx_drr}"
            context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {
                "bytes": img_bytes,
                "mime_type": "image/jpeg",
            }
            await update.message.reply_photo(photo=BytesIO(img_bytes), caption=cap[:200])
            await update.message.reply_text(
                "✅ Podés pedirme que la edite (ej. «edita esta imagen: …») o /editarimagen."
            )
            return
        await update.message.reply_text(
            f"El producto {idx_drr} no tiene imagen en la API o no se pudo descargar (reintento con Include=2 / Observaciones)."
        )
        return

    # =========================
    # EDICIÓN DE IMAGEN CON GEMINI (Nanobanana): si hay última imagen y el texto pide editarla
    # Funciona con texto o con transcripción de audio (ej: "edita esta imagen: poné un fondo de playa").
    # =========================
    edit_prompt = parse_edit_image_intent(text.strip())
    if edit_prompt and (context.user_data or {}).get(LAST_IMAGE_FOR_EDIT_KEY):
        await _do_edit_image(update, context, edit_prompt)
        return

    # =========================
    # BÚSQUEDA RÁPIDA POR VOZ (sin pasar por Claude)
    # Ejemplo: "producto leche entera" → /productos listar descripcion leche entera
    # Motivo: UX más rápida y determinista para consultas típicas de productos.
    # =========================
    t = text.strip()
    m = re.match(r"^(producto|productos)\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        servicio = _get_servicio_productos()
        if not servicio:
            await update.message.reply_text(
                "❌ DRR no configurado. Añadí DRR_API_BASE_URL en el .env del servidor. "
                "Ver docs/DOCUMENTACION_MIGRACION_DRR.md"
            )
            return
        query = m.group(2).strip()
        await update.message.reply_text("📋 Buscando productos...")
        try:
            listado, lista = await asyncio.to_thread(servicio.listar, descripcion=query, codigo_barras=None, limit=10)
            if len(lista) == 0:
                log_para_copiar = _build_drr_zero_log(servicio, descripcion=query, codigo_barras=None)
                context.user_data["drr_last_zero_log"] = log_para_copiar
                keyboard = InlineKeyboardMarkup.from_button(
                    InlineKeyboardButton("📋 Log para copiar", callback_data="drr_log_copy")
                )
                await update.message.reply_text(listado[:4000], reply_markup=keyboard)
            else:
                await update.message.reply_text(listado[:4000])
        except Exception as e:
            logger.exception("Error búsqueda por voz producto: %s", e)
            await update.message.reply_text(f"❌ Error buscando productos: {e}")
        return

    # Registro en memoria: distingue mensaje escrito de voz para el log
    clean_text = text.strip()
    append_log("user", clean_text, entry_type="voz" if from_voice else "message")

    # Si el usuario está respondiendo a un mensaje previo (reply_to_message),
    # incluir ese contenido explícitamente en lo que se envía a Claude/OpenClaw.
    replied = getattr(update.message, "reply_to_message", None) if update and update.message else None
    user_for_ai = clean_text
    if replied:
        replied_text = (getattr(replied, "text", None) or getattr(replied, "caption", None) or "").strip()
        if not replied_text:
            # Mensajes especiales: documento, foto, resultado de comando sin texto claro
            doc = getattr(replied, "document", None)
            if doc and getattr(doc, "file_name", None):
                replied_text = f"[Documento: {doc.file_name}]"
        if replied_text:
            # Claude ve claramente a qué mensaje se está respondiendo
            user_for_ai = (
                "Estoy respondiendo a este mensaje anterior:\n"
                "-----\n"
                f"{replied_text[:800]}\n"
                "-----\n\n"
                "Mi mensaje ahora es:\n"
                f"{clean_text}"
            )

    context_memory = get_recent_logs_for_context(days=3, max_chars=3500)
    await update.message.reply_text("🧠 pensando...")

    try:
        response = await get_ai_response(user_for_ai, context_memory=context_memory)

        cal_j = google_workspace.extract_json_after_marker(response, "CALENDAR_PROPUESTA:")
        reply_rest = (
            google_workspace.strip_marker_and_json(response, "CALENDAR_PROPUESTA:")
            if cal_j
            else response
        )
        if cal_j:
            try:
                payload = google_workspace.normalize_calendar_payload(cal_j)
            except Exception as e:
                await update.message.reply_text(f"❌ Calendario (datos inválidos): {e}")
                if reply_rest.strip():
                    await update.message.reply_text(strip_markdown_display_symbols(reply_rest)[:4000])
                return
            if not google_workspace.oauth_configured():
                await update.message.reply_text(
                    "📅 Falta configurar Google OAuth en el servidor. Ver docs/GOOGLE_CALENDAR_DRIVE.md"
                )
                if reply_rest.strip():
                    await update.message.reply_text(strip_markdown_display_symbols(reply_rest)[:4000])
                return
            if not google_workspace.is_authorized():
                prefix = (os.getenv("JARVIS_PUBLIC_BASE_URL") or "").strip().rstrip("/") or "http://TU_VPS"
                await update.message.reply_text(
                    "📅 Conectá Google una vez en el navegador:\n"
                    f"{prefix}/admin/google/oauth/start?token=(ADMIN_PANEL_TOKEN)"
                )
                if reply_rest.strip():
                    await update.message.reply_text(strip_markdown_display_symbols(reply_rest)[:4000])
                return
            context.user_data["pending_calendar_event"] = payload
            await update.message.reply_text(
                "📅 ¿Lo agendo en tu Google Calendar?\n\n"
                + google_workspace.format_event_for_user(payload)
                + "\n\nRespondé SI para confirmar o NO para cancelar."
            )
            rr = reply_rest.strip()
            if rr.startswith("NOTA:"):
                note_text = rr.replace("NOTA:", "", 1).strip()
                path = save_note(note_text)
                append_log("assistant", f"Nota guardada: {note_text}", entry_type="nota")
                await update.message.reply_text(
                    f"📝 Nota guardada en:\n{path}\n\n{strip_markdown_display_symbols(note_text)}"
                )
            elif rr:
                await update.message.reply_text(strip_markdown_display_symbols(rr)[:4000])
            return

        dm = re.search(r"(?mi)^DRIVE_SUBIR:\s*(\S+)\s*$", response)
        if dm:
            rel = dm.group(1).strip()
            rest = re.sub(r"(?mi)^DRIVE_SUBIR:\s*\S+\s*", "", response, count=1).strip()
            path = _telegram_drive_resolve_path(rel)
            if not path or not path.is_file():
                await update.message.reply_text(f"❌ No encuentro el archivo: `{rel}`")
                if rest:
                    await update.message.reply_text(strip_markdown_display_symbols(rest)[:4000])
                return
            if not google_workspace.oauth_configured() or not google_workspace.is_authorized():
                prefix = (os.getenv("JARVIS_PUBLIC_BASE_URL") or "").strip().rstrip("/") or "http://TU_VPS"
                await update.message.reply_text(
                    f"📁 Conectá Google primero: {prefix}/admin/google/oauth/start?token=(ADMIN_PANEL_TOKEN)"
                )
                if rest:
                    await update.message.reply_text(strip_markdown_display_symbols(rest)[:4000])
                return
            try:
                up = google_workspace.upload_file_to_drive(path, drive_name=path.name)
                link = (up.get("webViewLink") or "").strip()
                msg = f"✅ Subido a Drive: {up.get('name')}" + (f"\n{link}" if link else "")
                await update.message.reply_text(msg)
            except Exception as e:
                logger.exception("Telegram Drive upload")
                await update.message.reply_text(f"❌ Error subiendo a Drive: {e}")
            if rest:
                await update.message.reply_text(strip_markdown_display_symbols(rest)[:4000])
        return

        if response.startswith("NOTA:"):
            note_text = response.replace("NOTA:", "", 1).strip()
            path = save_note(note_text)
            append_log("assistant", f"Nota guardada: {note_text}", entry_type="nota")
            await update.message.reply_text(
                f"📝 Nota guardada en:\n{path}\n\n{strip_markdown_display_symbols(note_text)}"
            )
            return

        # DRR PRODUCTOS: el modelo debe devolver una línea con "PRODUCTOS: descripcion | cantidad".
        # A veces aparece con typo ("PRODUOTOS") y/o espacios: lo toleramos.
        first_line = response.strip().splitlines()[0].strip() if response.strip() else ""
        first_line_clean = first_line.replace("```", "").strip()
        m_prod = re.match(
            r"^(PRODUCTOS|PRODUOTOS)\s*:\s*(.*)\s*$",
            first_line_clean,
            flags=re.IGNORECASE,
        )
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
                "[%s] DRR filtros (from Telegram): desc=%r limit=%s include_prices=%s order=%r solo_lista=%s",
                update.effective_chat.id if update.effective_chat else "unknown",
                desc,
                final_limit,
                final_include_prices,
                final_order,
                final_solo_lista,
            )
            append_log(
                "sistema",
                f"DRR filtros (Telegram): desc={desc!r} limit={final_limit} include_prices={final_include_prices} order={final_order!r} solo_lista={final_solo_lista!r}",
            )

            await update.message.reply_text(f"📦 Buscando productos: {desc or 'todos'} (limit={final_limit})...")
            resultado = _get_productos(
                descripcion=desc,
                limit=final_limit,
                include_prices=final_include_prices,
                order=final_order,
                solo_lista_precio_id=final_solo_lista,
            )
            append_log("assistant", f"PRODUCTOS => {resultado[:200]}", entry_type="producto")
            try:
                from drr.api_client import DRRProductoAPIClient

                repo = DRRProductoAPIClient(DRR_API_BASE_URL, api_key=DRR_API_KEY or None, cache_ttl_seconds=25)
                fetch_limit = final_limit if final_order is None else max(final_limit, 50)
                plist = repo.listar(descripcion=desc or None, limit=fetch_limit)
                if final_order in ("last_modified_desc", "last_modified_asc"):
                    plist = _sort_products_by_last_modified(plist, final_order)
                shown = plist[:final_limit]
                context.user_data["drr_last_products_snap"] = [p.to_snapshot() for p in shown]
            except Exception:
                context.user_data["drr_last_products_snap"] = []
            out_txt = f"📦 Productos DRR:\n{resultado}"
            if context.user_data.get("drr_last_products_snap"):
                out_txt += (
                    "\n\n_Para ver la imagen de un ítem: «imagen del producto 1» o «foto del primero»._"
                )
            await update.message.reply_text(strip_markdown_display_symbols(out_txt)[:4000])
            return

        if "PRODUCTO_IMAGEN:" in response.upper():
            m = re.search(r"PRODUCTO_IMAGEN:\s*(\d+)", response, flags=re.IGNORECASE)
            if m:
                try:
                    n = int(m.group(1))
                except Exception:
                    n = 0
                if n > 0:
                    snaps = (context.user_data or {}).get("drr_last_products_snap") or []
                    if not snaps:
                        await update.message.reply_text(
                            "No hay listado de productos reciente. Pedime primero productos (ej. «traeme 5 martillos»)."
                        )
                        return
                    if n < 1 or n > len(snaps):
                        await update.message.reply_text(
                            f"En el último listado solo hay {len(snaps)} producto(s)."
                        )
                        return
                    row = snaps[n - 1]
                    if not DRR_API_BASE_URL:
                        await update.message.reply_text("❌ DRR no está configurado (DRR_API_BASE_URL).")
                        return
                    await update.message.reply_text("🖼 Descargando imagen del producto…")
                    from drr.api_client import fetch_product_image_bytes_for_snapshot

                    img_bytes = await asyncio.to_thread(
                        fetch_product_image_bytes_for_snapshot,
                        row,
                        base_url=DRR_API_BASE_URL,
                        api_key=DRR_API_KEY or None,
                    )
                    if not img_bytes:
                        await update.message.reply_text(
                            "No pude obtener la imagen (reintento con Include=2 / Observaciones en la API DRR)."
                        )
                        return
                    desc = (row.get("descripcion") or "")[:100]
                    cap = f"Producto {n}: {desc}" if desc else f"Producto {n}"
                    context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {
                        "bytes": img_bytes,
                        "mime_type": "image/jpeg",
                    }
                    await update.message.reply_photo(photo=BytesIO(img_bytes), caption=cap[:200])
                    await update.message.reply_text(
                        "✅ Podés pedirme que la edite (ej. «edita esta imagen: …») o /editarimagen."
                    )
                    return

        if "BUSCAR_IMAGEN:" in response:
            m = re.search(r"BUSCAR_IMAGEN:\s*(.+)", response, re.DOTALL)
            if m:
                query_img = m.group(1).strip().split("\n")[0].strip()  # una sola línea
                if query_img:
                    await update.message.reply_text("🔍 Buscando imagen en internet...")
                    img_bytes, mime = await asyncio.to_thread(_search_web_image_and_download, query_img)
                    if img_bytes:
                        context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": img_bytes, "mime_type": mime}
                        await update.message.reply_photo(photo=BytesIO(img_bytes), caption=f"Búsqueda: {query_img[:100]}")
                        await update.message.reply_text(
                            "✅ Imagen guardada. Decime cómo editarla (ej: «agregale un logo que diga ferretería rubencito») o usá /editarimagen."
                        )
                        append_log("sistema", f"BUSCAR_IMAGEN: {query_img[:60]} -> imagen guardada para editar", entry_type="imagen")
                    else:
                        await update.message.reply_text("No encontré ninguna imagen para esa búsqueda. Probá con otra frase o enviame una foto.")
                return
            # Si no había query válida, seguir como respuesta normal
        m_img = re.search(r"(?s)IMAGEN\s*:\s*(.+)", response)
        if m_img:
            prompt_imagen = m_img.group(1).strip()
            # Evitar usar como prompt texto de ayuda (listas de comandos, AUDIO:, BUSCAR:, etc.)
            if any(
                x in prompt_imagen
                for x in ("AUDIO:", "BUSCAR:", "CMD:", "NOTA:", "Logs", "Proyectos")
            ):
                append_log("assistant", response)
                await update.message.reply_text(strip_markdown_display_symbols(response)[:4000])
                return
            append_log("assistant", f"Generando imagen: {prompt_imagen[:100]}...", entry_type="imagen")
            await _do_generate_image(update, context, prompt_imagen)
            return

        if "AUDIO:" in response:
            m = re.search(r"AUDIO:\s*(.+)", response, re.DOTALL)
            if m:
                texto_audio = m.group(1).strip()
                append_log("assistant", f"Generando audio: {texto_audio[:80]}...", entry_type="audio")
                await _do_generate_audio(update, context, texto_audio)
                return

        if "BUSCAR:" in response:
            m = re.search(r"BUSCAR:\s*(.+)", response, re.DOTALL)
            if m:
                query = m.group(1).strip()
                await update.message.reply_text("🔍 Buscando en internet...")
                try:
                    results_text = await search_web(query)
                    append_log("sistema", f"Búsqueda: {query[:60]}...", entry_type="busqueda")
                    prompt_con_resultados = (
                        f"El usuario preguntó: {text.strip()}\n\n"
                        f"Se buscó en internet con la consulta: {query}\n\n"
                        "Información encontrada:\n"
                        f"{results_text}\n\n"
                        "Resumí o respondé en español según esta información. Si no hay nada relevante, decilo brevemente."
                    )
                    respuesta_final = await get_ai_response(prompt_con_resultados, context_memory=None)
                    append_log("assistant", respuesta_final[:200] + ("..." if len(respuesta_final) > 200 else ""))
                    await update.message.reply_text(strip_markdown_display_symbols(respuesta_final)[:4000])
                except Exception as e:
                    logger.exception("Error en búsqueda: %s", e)
                    await update.message.reply_text(f"❌ Error al buscar: {e}")
                return

        if "CMD:" in response:
            lines = response.splitlines()
            accion = ""
            cmd = ""
            for line in lines:
                line = line.strip()
                if line.startswith("ACCION:"):
                    accion = line.replace("ACCION:", "", 1).strip()
                elif line.startswith("CMD:"):
                    cmd = line.replace("CMD:", "", 1).strip()
            # Si Claude mencionó CMD: pero no en una línea válida, buscar "CMD: comando" en cualquier línea
            if not cmd and "CMD:" in response:
                m = re.search(r"CMD:\s*(.+?)(?:\n|$)", response, re.DOTALL)
                if m:
                    cmd = m.group(1).strip()
            # Si aún no hay comando, no mostrar error: tratar la respuesta como texto normal
            if not cmd:
                append_log("assistant", response)
                await update.message.reply_text(strip_markdown_display_symbols(response)[:4000])
                return
            code = str(random.randint(1000, 9999))
            pending_command = cmd
            pending_code = code
            msg = (
                f"⚠️ Acción detectada\n\n"
                f"Acción: {accion or 'sin descripción'}\n"
                f"Comando: {cmd}\n\n"
                f"Confirmá con:\n/confirm {code}"
            )
            append_log("assistant", f"Propuesta de comando: {cmd}", entry_type="comando")
            await update.message.reply_text(msg)
            return

        append_log("assistant", response)
        await update.message.reply_text(strip_markdown_display_symbols(response)[:4000])

    except Exception as e:
        append_log("sistema", f"Error: {e}", entry_type="error")
        logger.exception("Error procesando mensaje: %s", e)
        await update.message.reply_text(f"❌ error procesando texto: {e}")


async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    UN SOLO handler para todo mensaje que no sea comando: texto, voz, audio, documento de audio.
    Así ningún mensaje se pierde por filtros. Siempre entramos acá y decidimos qué hacer.
    """
    if not update.message:
        return
    msg = update.message
    voice = getattr(msg, "voice", None)
    audio = getattr(msg, "audio", None)
    doc = getattr(msg, "document", None)
    text = (msg.text or "").strip()

    # Log SIEMPRE para ver en consola qué está llegando
    logger.info(
        ">>> MENSAJE RECIBIDO: voice=%s audio=%s doc_audio=%s text_len=%s",
        bool(voice), bool(audio), bool(doc and (doc.mime_type or "").startswith("audio/")), len(text),
    )

    if not is_authorized(update):
        await msg.reply_text("⛔ chat no autorizado")
        return
    if not session_ok():
        await msg.reply_text("🔒 primero /login TU_CLAVE")
        return

    # --- Foto: guardar como imagen para editar con Gemini (Nanobanana) ---
    photo = getattr(msg, "photo", None)
    if photo and len(photo) > 0:
        # Telegram envía varias resoluciones; la última es la más grande.
        largest = photo[-1]
        if largest.file_size and largest.file_size > MAX_IMAGE_EDIT_BYTES:
            await msg.reply_text(f"La imagen es muy pesada (máx {MAX_IMAGE_EDIT_BYTES // (1024*1024)} MB). Enviá una más chica.")
            return
        await msg.reply_text("📥 Descargando imagen...")
        try:
            tg_file = await context.bot.get_file(largest.file_id)
            buf = BytesIO()
            await tg_file.download_to_memory(buf)
            buf.seek(0)
            img_bytes = buf.getvalue()
        except Exception as e:
            logger.warning("Error descargando foto: %s", e)
            await msg.reply_text(f"❌ No pude descargar la imagen: {e}")
            return
        if len(img_bytes) > MAX_IMAGE_EDIT_BYTES:
            await msg.reply_text("La imagen pesa demasiado para editarla. Enviá una más chica.")
            return
        mime = "image/jpeg"
        context.user_data[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": img_bytes, "mime_type": mime}
        await msg.reply_text(
            "✅ Imagen guardada para editar con Gemini (Nanobanana).\n\n"
            "Decime con texto o audio cómo querés editarla (ej: «cambia el fondo a una playa») o usá /editarimagen y tu instrucción."
        )
        return

    # --- Es voz o audio: transcribir y procesar como texto ---
    if voice or audio or (doc and (doc.mime_type or "").startswith("audio/")):
        if voice:
            file_id, file_unique_id = voice.file_id, voice.file_unique_id
            ext = ".ogg"
            duration_sec = getattr(voice, "duration", None) or 0
        elif audio:
            file_id, file_unique_id = audio.file_id, audio.file_unique_id
            ext = ".ogg" if (audio.mime_type or "").endswith("ogg") else ".mp3"
            duration_sec = getattr(audio, "duration", None) or 0
        else:
            file_id, file_unique_id = doc.file_id, doc.file_unique_id
            ext = ".ogg" if "ogg" in (doc.mime_type or "") else ".mp3"
            duration_sec = 0  # documento sin duración conocida

        # Rechazar audios muy largos para evitar crash/OOM en Whisper
        MAX_VOICE_DURATION_SEC = 120
        if duration_sec > MAX_VOICE_DURATION_SEC:
            await msg.reply_text(
                f"⏱ El audio es muy largo ({duration_sec}s). Enviá uno de hasta {MAX_VOICE_DURATION_SEC} segundos."
            )
            return

        user_id = update.effective_user.id if update.effective_user else update.effective_chat.id
        if user_id in _voice_busy:
            await msg.reply_text("⏳ Estoy procesando tu audio anterior. Esperá a que termine la respuesta.")
            return
        _voice_busy.add(user_id)

        audio_path = None
        ud = context.user_data or {}

        # =========================
        # Flujo Gemini Live (voz → voz) si el usuario activó "cambiar a Gemini"
        # =========================
        if ud.get(USE_GEMINI_VOICE_KEY) and GEMINI_API_KEY:
            await msg.reply_text("🎤 Respondiendo con Gemini...")
            try:
                tg_file = await context.bot.get_file(file_id)
                audio_path = VOICE_TEMP_DIR / f"voice_{update.effective_chat.id}_{file_unique_id}{ext}"
                await tg_file.download_to_drive(audio_path)
                audio_bytes = audio_path.read_bytes()
                pcm_16k = await asyncio.to_thread(_ogg_to_pcm_16k, None, audio_path)
                if not pcm_16k:
                    pcm_16k = await asyncio.to_thread(_ogg_to_pcm_16k, audio_bytes, None)
                if pcm_16k:
                    response_pcm = await asyncio.wait_for(
                        _gemini_live_voice_response(pcm_16k, ""),
                        timeout=90,
                    )
                    if response_pcm:
                        ogg_bytes = await asyncio.to_thread(_pcm_24k_to_ogg, response_pcm)
                        if ogg_bytes:
                            await msg.reply_voice(voice=BytesIO(ogg_bytes))
                            append_log("user", "(mensaje de voz)", entry_type="voz")
                            append_log("sistema", "Respuesta de voz con Gemini Live", entry_type="voz")
                        else:
                            await msg.reply_text("❌ No pude convertir la respuesta de Gemini a audio. Probá de nuevo o usá /cambiargemini para volver a Claude.")
                    else:
                        await msg.reply_text("❌ Gemini no devolvió audio. Probá de nuevo o usá /cambiargemini para volver a Claude.")
                else:
                    await msg.reply_text("❌ No pude convertir tu audio (¿ffmpeg instalado?). Usá /cambiargemini para volver a Claude y transcribir.")
            except asyncio.TimeoutError:
                await msg.reply_text("⏱ Gemini tardó demasiado. Probá con un mensaje más corto o /cambiargemini para volver a Claude.")
            except Exception as e:
                logger.exception("Error Gemini Live voz: %s", e)
                await msg.reply_text(f"❌ Error con Gemini voz: {e}. Probá /cambiargemini para volver a Claude.")
            finally:
                _voice_busy.discard(user_id)
                if audio_path and audio_path.exists():
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass
            return

        await msg.reply_text("🎤 Escuchando y transcribiendo...")
        # Timeouts: API/N8N 50s; transcripción local 90s (primera vez carga modelo). Límite total para no colgar nunca.
        API_TIMEOUT = 50
        TRANSCRIBE_TIMEOUT = 90
        TOTAL_VOICE_TIMEOUT = 95  # Techo: download + transcribe; si se pasa, respondemos y liberamos.

        _audit_log("voice_start", "", user_id=user_id, has_api=bool(TRANSCRIBE_API_URL), duration_sec=duration_sec)

        async def _do_download_and_transcribe() -> str | None:
            """Descarga el audio de Telegram y transcribe (API o local). None si sin voz o error interno."""
            nonlocal audio_path
            tg_file = await context.bot.get_file(file_id)
            audio_path = VOICE_TEMP_DIR / f"voice_{update.effective_chat.id}_{file_unique_id}{ext}"
            await tg_file.download_to_drive(audio_path)
            audio_bytes = audio_path.read_bytes()

            transcribed: str | None = None
            if TRANSCRIBE_API_URL:
                try:
                    result, _ = await asyncio.wait_for(
                        asyncio.to_thread(_transcribe_via_api, audio_bytes, f"voice{ext}"),
                        timeout=API_TIMEOUT,
                    )
                    transcribed = result
                except asyncio.TimeoutError:
                    _audit_log("voice_api_timeout", "", user_id=user_id, timeout_sec=API_TIMEOUT)
                    logger.warning("Transcripción API/N8N superó timeout de %s s; fallback a local", API_TIMEOUT)
                if transcribed is None:
                    transcribed = await asyncio.wait_for(
                        asyncio.to_thread(_transcribe_local, str(audio_path)),
                        timeout=TRANSCRIBE_TIMEOUT,
                    )
                    _audit_log("voice_local_fallback", "tras API fallo o timeout", user_id=user_id)
            else:
                transcribed = await asyncio.wait_for(
                    asyncio.to_thread(_transcribe_local, str(audio_path)),
                    timeout=TRANSCRIBE_TIMEOUT,
                )

            return transcribed

        try:
            transcribed = await asyncio.wait_for(_do_download_and_transcribe(), timeout=TOTAL_VOICE_TIMEOUT)
            logger.info("Transcripción finalizada. Audio transcrito: %s", (transcribed[:100] + "...") if transcribed and len(transcribed) > 100 else transcribed)
            if not transcribed or transcribed == "(sin voz detectada)":
                await msg.reply_text("No detecté voz en el audio. Probá hablar más claro o más cerca del micrófono.")
                append_log("sistema", "Audio sin voz detectada", entry_type="voz")
                return
            await _process_user_message(update, context, transcribed, from_voice=True)
        except asyncio.TimeoutError:
            _audit_log("voice_timeout", "límite total superado", user_id=user_id, timeout_sec=TOTAL_VOICE_TIMEOUT)
            logger.warning("Procesamiento de audio superó timeout total de %s s", TOTAL_VOICE_TIMEOUT)
            append_log("sistema", f"Transcripción timeout ({TOTAL_VOICE_TIMEOUT}s)", entry_type="error")
            await msg.reply_text("⏱ El audio tardó demasiado. Probá con un mensaje más corto o reintentá.")
        except Exception as e:
            _audit_log("voice_error", str(e)[:200], user_id=user_id)
            logger.exception("Error al procesar audio: %s", e)
            append_log("sistema", f"Error transcribiendo audio: {e}", entry_type="error")
            await msg.reply_text(f"❌ Error al transcribir el audio: {e}")
        finally:
            _voice_busy.discard(user_id)
            if audio_path and audio_path.exists():
                try:
                    audio_path.unlink()
                except OSError:
                    pass
        return

    # --- Es texto ---
    if text:
        await _process_user_message(update, context, text, from_voice=False)
        return

    await msg.reply_text("No puedo procesar este tipo de mensaje. Enviá texto o un audio/voz.")


async def callback_drr_img_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navegar entre imágenes de la última búsqueda (◀ Anterior / Siguiente ▶)."""
    await update.callback_query.answer()
    data = update.callback_query.data or ""
    urls = (context.user_data or {}).get("drr_image_search_urls") or []
    if not urls:
        drr_log("callback_img_nav", "sin drr_image_search_urls en user_data", level="WARNING")
        await update.effective_message.reply_text("No hay búsqueda reciente. Usá /productos imagen buscar <consulta>.")
        return
    idx = (context.user_data or {}).get("drr_image_search_index", 0)
    total = len(urls)
    is_prev = data == "drr_img_prev"
    idx = (idx - 1) % total if is_prev else (idx + 1) % total
    context.user_data["drr_image_search_index"] = idx
    url = urls[idx]
    id_codigo = (context.user_data or {}).get("drr_image_search_id_codigo")
    caption = f"{idx + 1}/{total} — Para guardar: /productos imagen guardar <id o código> [índice]"
    if id_codigo:
        caption = f"{idx + 1}/{total} — Para guardar: /productos imagen guardar {id_codigo} {idx}"
    try:
        await update.callback_query.edit_message_media(media=InputMediaPhoto(media=url))
        await update.callback_query.edit_message_caption(caption=caption)
        drr_log("callback_img_nav", f"dir={'prev' if is_prev else 'next'} -> índice {idx + 1}/{total}")
    except TelegramError as e:
        drr_log("callback_img_nav", f"TelegramError: {e}", level="ERROR")
        logger.warning("Error editando imagen en navegación: %s", e)
        await update.effective_message.reply_text(f"No se pudo cambiar la imagen: {e}")


async def callback_drr_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buscar imagen para el producto N de la última lista (botón 🖼 N)."""
    await update.callback_query.answer()
    data = update.callback_query.data
    if not data or not data.startswith("drr_buscar_"):
        return
    try:
        i = int(data.replace("drr_buscar_", ""), 10)
    except ValueError:
        drr_log("callback_buscar", f"callback_data inválido: {data!r}", level="WARNING")
        return
    lista = (context.user_data or {}).get("drr_last_lista") or []
    if i < 0 or i >= len(lista):
        drr_log("callback_buscar", f"índice {i} fuera de rango (lista len={len(lista)})", level="WARNING")
        await update.effective_message.reply_text("Esa posición ya no está disponible. Volvé a listar productos.")
        return
    servicio = _get_servicio_productos()
    if not servicio:
        drr_log("callback_buscar", "DRR no configurado", level="WARNING")
        await update.effective_message.reply_text("❌ DRR no configurado.")
        return
    prod = lista[i]
    query = " ".join(filter(None, [str(prod.get("codigo_barras") or "").strip(), (prod.get("descripcion") or "").strip()])).strip()
    if not query:
        query = str(prod.get("id") or "")
    drr_log("callback_buscar", f"índice={i} id={prod.get('id')} query={query[:80]!r}")
    try:
        resultados = await asyncio.to_thread(servicio.buscar_imagen, query, 5)
    except Exception as e:
        drr_log("callback_buscar", f"buscar_imagen falló: {e}", level="ERROR")
        await update.effective_message.reply_text(f"❌ Error al buscar imágenes: {e}")
        return
    context.user_data["drr_last_images"] = resultados
    urls = [r.get("image") or r.get("url") for r in resultados if r.get("image") or r.get("url")]
    context.user_data["drr_image_search_urls"] = urls
    context.user_data["drr_image_search_index"] = 0
    context.user_data["drr_image_search_id_codigo"] = str(prod.get("id") or prod.get("codigo_barras") or "")
    if not resultados:
        drr_log("callback_buscar", f"query={query[:60]!r} -> 0 resultados")
        await update.effective_message.reply_text("No se encontraron imágenes para este producto.")
        return
    first = resultados[0]
    url = first.get("image") or first.get("url")
    if not url:
        await update.effective_message.reply_text("La búsqueda no devolvió URLs.")
        return
    img_bytes = await asyncio.to_thread(_download_image_bytes, url)
    if not img_bytes:
        await update.effective_message.reply_text(f"Imagen encontrada (no se pudo descargar): {url[:80]}…")
        return
    drr_log("callback_buscar", f"ok índice={i} -> {len(urls)} imágenes enviadas")
    nav_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("◀ Anterior", callback_data="drr_img_prev"),
            InlineKeyboardButton("Siguiente ▶", callback_data="drr_img_next"),
        ]
    ])
    id_codigo = context.user_data.get("drr_image_search_id_codigo", "")
    caption = f"1/{len(resultados)} — Para guardar: /productos imagen guardar {id_codigo} [índice]"
    await update.effective_message.reply_photo(
        photo=BytesIO(img_bytes),
        caption=caption,
        reply_markup=nav_buttons,
    )


async def callback_drr_log_copy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Al pulsar 'Log para copiar', envía el log como texto y como .txt (Telegram no puede escribir en el portapapeles)."""
    await update.callback_query.answer()
    log = (context.user_data or {}).get("drr_last_zero_log")
    if not log:
        await update.effective_message.reply_text("No hay log guardado. Hacé otra búsqueda sin resultados y usá el botón de nuevo.")
        return
    # Instrucción: en Telegram el usuario debe seleccionar el texto o abrir el archivo y copiar
    await update.effective_message.reply_text(
        "📋 Para copiar: mantené apretado el mensaje de abajo → Copiar. "
        "O abrí el archivo .txt que envío después y copiá desde ahí."
    )
    # Mensaje con el log (seleccionable)
    await update.effective_message.reply_text(log[:4000] if len(log) <= 4000 else log[:3997] + "…")
    # Archivo .txt: en muchos clientes es más fácil abrirlo y copiar todo
    log_bytes = log.encode("utf-8")
    await update.effective_message.reply_document(
        document=BytesIO(log_bytes),
        filename="drr_log.txt",
        caption="Abrí el archivo y copiá el contenido al portapapeles.",
    )


# =========================
# MAIN
# =========================

def _acquire_lock() -> bool:
    """Solo una instancia del bot. Devuelve True si conseguimos el lock, False si ya hay otra corriendo."""
    pid = os.getpid()
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            # ¿Ese proceso sigue vivo?
            try:
                os.kill(old_pid, 0)
                return False  # sigue corriendo, no podemos arrancar
            except OSError:
                pass  # proceso muerto, lock obsoleto
        except (ValueError, OSError):
            pass
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
    try:
        LOCK_FILE.write_text(str(pid))
        atexit.register(lambda: LOCK_FILE.unlink() if LOCK_FILE.exists() else None)
        return True
    except OSError:
        return False


def main():
    if not _acquire_lock():
        logger.error(
            "Ya hay otra instancia del bot corriendo. Telegram solo permite una (409 Conflict). "
            "Matá el otro proceso: pkill -f jarvis_bot.py"
        )
        raise SystemExit(1)
    # Al iniciar: quitar webhook (si existe) para que el POLLING reciba todos los mensajes.
    # Si el bot tiene webhook activo, getUpdates no devuelve nada y los audios "no hacen nada".
    async def post_init(application):
        bot = application.bot
        try:
            info = await bot.get_webhook_info()
            if info.url:
                await bot.delete_webhook(drop_pending_updates=False)
                logger.warning("Webhook eliminado (estaba %s). Ahora el bot usa solo polling.", info.url)
            else:
                logger.info("Sin webhook activo; polling listo para recibir mensajes.")
        except Exception as e:
            logger.warning("No se pudo revisar/eliminar webhook: %s", e)
        await bot.set_my_commands([
            BotCommand("start", "Iniciar Jarvis"),
            BotCommand("login", "Login con clave"),
            BotCommand("authstatus", "Estado de sesión"),
            BotCommand("estado", "Estado del servidor"),
            BotCommand("confirm", "Confirmar comando pendiente"),
            BotCommand("cancel", "Cancelar acción pendiente"),
            BotCommand("log", "Ver log del día o fecha"),
            BotCommand("dias", "Listar días con log"),
            BotCommand("resumen", "Resumen de últimos N días"),
            BotCommand("proyecto", "Crear proyecto: Nombre | Descripción"),
            BotCommand("audio", "Generar audio (TTS) a partir de texto"),
            BotCommand("imagen", "Generar imagen con IA"),
            BotCommand("editarimagen", "Editar última imagen con Gemini (Nanobanana)"),
            BotCommand("buscar", "Buscar en internet"),
            BotCommand("cambiargemini", "Cambiar a Gemini para voz / volver a modo normal"),
            BotCommand("productos", "DRR: consultar productos, imágenes"),
            BotCommand("cripto", "Cripto: precios, top, simulado, swap Jupiter"),
        ])
        logger.info("Menú de comandos (/) actualizado en Telegram.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(CommandHandler("authstatus", authstatus))
    app.add_handler(CommandHandler("estado", estado))
    app.add_handler(CommandHandler("confirm", confirm))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("dias", cmd_dias))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("proyecto", cmd_proyecto))
    app.add_handler(CommandHandler("audio", cmd_audio))
    app.add_handler(CommandHandler("imagen", cmd_imagen))
    app.add_handler(CommandHandler("editarimagen", cmd_editarimagen))
    app.add_handler(CommandHandler("buscar", cmd_buscar))
    app.add_handler(CommandHandler("cambiargemini", cmd_cambiargemini))
    app.add_handler(CommandHandler("productos", cmd_productos))
    app.add_handler(CommandHandler("cripto", cmd_cripto))
    app.add_handler(CallbackQueryHandler(callback_drr_log_copy, pattern="^drr_log_copy$"))
    app.add_handler(CallbackQueryHandler(callback_drr_img_nav, pattern="^drr_img_(prev|next)$"))
    app.add_handler(CallbackQueryHandler(callback_drr_buscar, pattern="^drr_buscar_\\d+$"))

    # Un solo handler para CUALQUIER mensaje que no sea comando (voz, audio, documento, texto, etc.)
    # Así no se pierde ningún mensaje por filtros; dentro del handler decidimos qué hacer.
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~filters.COMMAND, handle_any_message))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Si cualquier handler falla (p. ej. voz), lo registramos y avisamos al usuario."""
        logger.exception("Error en el bot: update=%s context.error=%s", update, context.error)
        if isinstance(update, Update) and update.effective_message and context.error:
            try:
                await update.effective_message.reply_text(
                    f"❌ Error interno: {str(context.error)[:200]}. Revisá los logs del servidor."
                )
            except TelegramError:
                pass

    app.add_error_handler(on_error)
    logger.info("Jarvis bot iniciado (IA: %s). Esperando mensajes (ALLOWED_CHAT_ID=%s)...", BACKEND_NAME, ALLOWED_CHAT_ID)
    try:
        # message + callback_query para recibir mensajes y pulsaciones en botones inline (productos, imágenes)
        app.run_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.exception("Error al ejecutar el bot: %s", e)
        raise

if __name__ == "__main__":
    main()
