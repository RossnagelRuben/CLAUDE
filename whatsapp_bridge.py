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
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request

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
AGENT_SECRET = os.getenv("AGENT_SECRET", "").strip()
PROMPT_FILE = BASE_DIR / "agent_prompt.txt"
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)

# Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").strip()   # ej: http://localhost:8080
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "").strip()   # apikey de Evolution
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "jarvis").strip()

# Números autorizados: formato 549XXXXXXXXXX (sin @s.whatsapp.net, sin +)
# Separados por coma. Si está vacío, solo bloquea — SIEMPRE definir esto.
WHATSAPP_ALLOWED = {
    n.strip() for n in os.getenv("WHATSAPP_ALLOWED_NUMBERS", "").split(",") if n.strip()
}

SESSION_HOURS = int(os.getenv("WHATSAPP_SESSION_HOURS", "8"))

if not CLAUDE_API_KEY:
    raise RuntimeError("Falta CLAUDE_API_KEY en .env")
if not AGENT_SECRET:
    raise RuntimeError("Falta AGENT_SECRET en .env")

claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
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
        "Guardá notas con NOTA: y realizá búsquedas con BUSCAR:."
    )


def _ask_claude(text: str, phone: str) -> str:
    """Llama a Claude con historial de conversación del número."""
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


def _send(jid: str, text: str) -> None:
    """Envía un mensaje de texto via Evolution API."""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        logger.warning("Evolution API no configurada; respuesta no enviada: %s", text[:80])
        return
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    try:
        resp = httpx.post(
            url,
            json={"number": jid, "text": text},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Error enviando WhatsApp a %s: %s", jid, e)


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
        if text.strip() == AGENT_SECRET:
            _open_session(phone)
            _send(jid, f"✅ Sesión iniciada. Hola, soy Jarvis. Sesión válida por {SESSION_HOURS}h.")
        else:
            _send(jid, "🔒 Ingresá la clave de acceso para usar Jarvis.")
        return

    # --- Confirmación de comando pendiente ---
    if phone in _pending_cmd:
        cmd = _pending_cmd.pop(phone)
        if text.strip().upper() in ("SI", "SÍ", "S", "YES", "Y"):
            _send(jid, f"⚙️ Ejecutando: `{cmd}`")
            output = _run_command(cmd)
            _send(jid, f"✅ Resultado:\n```\n{output[:3000]}\n```")
        else:
            _send(jid, "❌ Comando cancelado.")
        return

    # --- Claude ---
    logger.info("[%s] → %s", phone, text[:80])
    reply = _ask_claude(text, phone)
    logger.info("[%s] ← %s", phone, reply[:80])

    # --- Parseo de prefijos especiales ---

    if reply.startswith("NOTA:"):
        note = reply.replace("NOTA:", "", 1).strip()
        fname = BASE_DIR / "notes" / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fname.write_text(note, encoding="utf-8")
        _send(jid, f"📝 Nota guardada:\n{note}")
        return

    if reply.startswith(("ACCION:", "CMD:")):
        prefix = "ACCION:" if reply.startswith("ACCION:") else "CMD:"
        cmd = reply.replace(prefix, "", 1).strip()
        _pending_cmd[phone] = cmd
        _send(jid, f"⚠️ Jarvis quiere ejecutar:\n`{cmd}`\n\nRespondé *SI* para confirmar o cualquier otra cosa para cancelar.")
        return

    if reply.startswith("BUSCAR:"):
        query = reply.replace("BUSCAR:", "", 1).strip()
        _send(jid, f"🔍 Buscando: {query}...")
        results = _search_web(query)
        prompt = (
            f"Resultados de búsqueda para '{query}':\n\n{results}\n\n"
            "Resumí o respondé en español según esta información."
        )
        final = _ask_claude(prompt, phone)
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
        "claude": bool(CLAUDE_API_KEY),
        "evolution_url": EVOLUTION_API_URL or "(no configurado)",
        "instance": EVOLUTION_INSTANCE,
        "allowed_numbers": len(WHATSAPP_ALLOWED) if WHATSAPP_ALLOWED else "⚠ ninguno definido",
        "active_sessions": len([p for p, exp in _sessions.items() if datetime.now() < exp]),
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

    if key.get("fromMe"):
        return {"ok": True}

    jid: str = key.get("remoteJid", "")
    phone = jid.replace("@s.whatsapp.net", "").replace("@g.us", "").lstrip("+")

    # Ignorar grupos
    if "@g.us" in jid:
        return {"ok": True}

    # Verificar número autorizado
    if WHATSAPP_ALLOWED and phone not in WHATSAPP_ALLOWED:
        logger.info("Número no autorizado ignorado: %s", phone)
        return {"ok": True}

    message = data.get("message", {})
    text = (
        message.get("conversation")
        or message.get("extendedTextMessage", {}).get("text")
        or ""
    ).strip()

    if not text:
        return {"ok": True}

    # Procesar en thread para no bloquear el event loop de FastAPI
    await asyncio.to_thread(_process_message, text, phone, jid)
    return {"ok": True}
