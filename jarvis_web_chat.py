"""
Chat web Jarvis — pipeline completo alineado con Telegram (jarvis_web_pipeline / jarvis_bot).

- Autenticación: JARVIS_WEB_CHAT_KEY (por defecto 41419180 si la variable falta o está vacía) o AGENT_SECRET.
- Historial corto por session_id (UUID en el navegador) para contexto conversacional.
- Log append-only JSONL para diagnóstico y mejora continua (ver agent_data/logs/jarvis_web_chat.jsonl).
- Respuestas multimodal: ``parts`` (texto, imagen, audio, archivo) además de ``reply`` texto plano.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
# Log autoalimentado: una línea JSON por intercambio (retroalimentación / auditoría).
WEB_CHAT_JSONL = BASE_DIR / "agent_data" / "logs" / "jarvis_web_chat.jsonl"

# session_id -> lista de {role, text} (últimos turnos)
_SESSION_HISTORY: dict[str, list[dict[str, str]]] = {}
_MAX_TURNS = 10
_MAX_SESSIONS = 500


# Clave por defecto del chat web: **siempre** válida además de las del .env
# (así sigue funcionando aunque JARVIS_WEB_CHAT_KEY esté mal o sea otra).
_DEFAULT_JARVIS_WEB_CHAT_KEY = "41419180"


def _normalize_web_token(token: str) -> str:
    """Quita espacios/BOM/zero-width; corrige dígitos «anchos» típicos de copiar/pegar."""
    if not token:
        return ""
    t = token.strip()
    for z in ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\xa0"):
        t = t.replace(z, "")
    # Dígitos unicode de ancho completo → ASCII
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    t = t.translate(trans)
    return t.strip()


def _allowed_keys() -> list[str]:
    """Claves aceptadas (comparación en tiempo constante)."""
    out: list[str] = []
    # 1) Siempre la clave documentada del chat web
    out.append(_DEFAULT_JARVIS_WEB_CHAT_KEY)
    # 2) Opcional: otra clave solo para web (sin quitar la por defecto)
    raw = os.getenv("JARVIS_WEB_CHAT_KEY", "").strip()
    if raw and raw not in out:
        out.append(raw)
    # 3) Misma clave que el panel / agente si querés unificar
    ag = os.getenv("AGENT_SECRET", "").strip()
    if ag and ag not in out:
        out.append(ag)
    return out


def verify_web_chat_token(token: str) -> bool:
    t = _normalize_web_token(token)
    if not t:
        return False
    for k in _allowed_keys():
        if len(t) != len(k):
            continue
        try:
            if secrets.compare_digest(t, k):
                return True
        except (TypeError, ValueError):
            continue
    logger.debug(
        "jarvis_web_chat: token rechazado (len=%s)",
        len(t),
    )
    return False


def _trim_sessions() -> None:
    if len(_SESSION_HISTORY) <= _MAX_SESSIONS:
        return
    # Elimina ~20% de las sesiones más antiguas (orden arbitrario de dict en Py3.7+ es inserción)
    drop = len(_SESSION_HISTORY) - int(_MAX_SESSIONS * 0.8)
    for i, key in enumerate(list(_SESSION_HISTORY.keys())):
        if i >= drop:
            break
        _SESSION_HISTORY.pop(key, None)


def _append_jsonl(record: dict[str, Any]) -> None:
    WEB_CHAT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        with WEB_CHAT_JSONL.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("jarvis_web_chat: no se pudo escribir jsonl: %s", e)


def _history_block(session_id: str, history: list[dict[str, str]]) -> str:
    if not history:
        return ""
    lines = []
    for h in history[-_MAX_TURNS :]:
        role = h.get("role", "")
        text = (h.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            lines.append(f"Usuario: {text}")
        elif role == "assistant":
            lines.append(f"Jarvis: {text}")
    if not lines:
        return ""
    return "--- Historial reciente (misma sesión web) ---\n" + "\n".join(lines) + "\n\n"


def _parts_to_plain_reply(parts: list[dict[str, Any]]) -> str:
    """Un solo string para compatibilidad y para el historial (sin base64)."""
    lines: list[str] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            lines.append((p.get("text") or "").strip())
        elif t == "image":
            cap = (p.get("caption") or "").strip()
            lines.append(f"[Imagen] {cap}".strip())
        elif t == "audio":
            lines.append("[Audio]")
        elif t == "file":
            lines.append(f"[Archivo: {p.get('filename', 'adjunto')}]")
        elif t == "crypto_quote":
            sym = p.get("symbol") or "?"
            usd = p.get("price_usd") or ""
            lines.append(f"[Cripto {sym}] {usd}")
    out = "\n\n".join(x for x in lines if x)
    return out.strip() or "(sin texto)"


async def run_web_chat_turn(
    *,
    token: str,
    message: str,
    session_id: str,
    client_ip: str,
    image_base64: str | None = None,
    image_mime: str = "image/jpeg",
) -> dict[str, Any] | StreamingResponse:
    """
    Ejecuta un turno con el pipeline completo (cripto, DRR, imágenes, audio, búsqueda, comandos con confirmación).
    """
    t0 = time.perf_counter()
    msg = (message or "").strip()
    img_bytes: bytes | None = None
    if image_base64:
        try:
            img_bytes = base64.b64decode(image_base64, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="image_base64 inválido") from None
        if len(img_bytes) > 25 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="imagen demasiado grande")

    if not msg and not img_bytes:
        raise HTTPException(status_code=400, detail="mensaje vacío (o enviá una imagen)")

    if len(msg) > 12000:
        raise HTTPException(status_code=400, detail="mensaje demasiado largo")

    sid = (session_id or "").strip() or "default"
    if sid == "default":
        sid = "anon"

    hist = _SESSION_HISTORY.setdefault(sid, [])
    context_prefix = _history_block(sid, hist)
    combined = context_prefix + msg if context_prefix else msg

    try:
        from jarvis_web_pipeline import complete_web_tts, process_web_message
    except Exception as e:
        logger.exception("jarvis_web_chat: no se pudo importar jarvis_web_pipeline")
        _append_jsonl(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "import_error",
                "session_id": sid,
                "client_ip": client_ip,
                "error": str(e)[:500],
            }
        )
        raise HTTPException(status_code=503, detail="Jarvis no disponible (revisá pipeline y .env).") from e

    result = await process_web_message(
        sid,
        msg,
        combined_for_ai=combined,
        client_ip=client_ip,
        image_bytes=img_bytes,
        image_mime=image_mime or "image/jpeg",
    )

    parts = result.get("parts") or []
    meta = result.get("meta") or {}
    ok = result.get("ok", True)

    reply = _parts_to_plain_reply(parts if isinstance(parts, list) else [])

    # Con TTS pendiente el historial se escribe tras generar el MP3 (respuesta NDJSON en dos fases)
    if not meta.get("tts_pending"):
        hist.append({"role": "user", "text": msg + (" [imagen adjunta]" if img_bytes else "")})
        hist.append({"role": "assistant", "text": reply})
        if len(hist) > _MAX_TURNS * 2:
            del hist[: len(hist) - _MAX_TURNS * 2]
        _trim_sessions()

    dt_ms = int((time.perf_counter() - t0) * 1000)
    part_types = [p.get("type") for p in parts if isinstance(p, dict)] if parts else []

    if not ok:
        err = (result.get("error") or result.get("detail") or "error")[:800]
        _append_jsonl(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "pipeline_error",
                "session_id": sid,
                "client_ip": client_ip,
                "duration_ms": dt_ms,
                "meta": meta,
                "part_types": part_types,
                "user_preview": msg[:200],
                "error": err,
            }
        )
        raise HTTPException(status_code=500, detail=err)

    # Audio: enviar primero solo «Generando audio…» (NDJSON línea 1), luego MP3 (línea 2)
    if meta.get("tts_pending"):

        async def ndjson_audio():
            import json

            meta_first = {k: v for k, v in meta.items() if k != "tts_pending"}
            first = {
                "ok": True,
                "partial": True,
                "parts": parts,
                "session_id": sid,
                "meta": meta_first,
            }
            yield (json.dumps(first, ensure_ascii=False) + "\n").encode("utf-8")
            texto = meta.get("tts_pending") or ""
            await complete_web_tts(sid, parts, texto, meta)
            reply = _parts_to_plain_reply(parts if isinstance(parts, list) else [])
            hist.append({"role": "user", "text": msg + (" [imagen adjunta]" if img_bytes else "")})
            hist.append({"role": "assistant", "text": reply})
            if len(hist) > _MAX_TURNS * 2:
                del hist[: len(hist) - _MAX_TURNS * 2]
            _trim_sessions()
            dt_ms = int((time.perf_counter() - t0) * 1000)
            part_types = [p.get("type") for p in parts if isinstance(p, dict)] if parts else []
            _append_jsonl(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "chat_ok",
                    "session_id": sid,
                    "client_ip": client_ip,
                    "duration_ms": dt_ms,
                    "meta_action": meta.get("action"),
                    "ai_markers": meta.get("ai_markers"),
                    "ai_response_chars": meta.get("ai_response_chars"),
                    "audio_tts_ms": meta.get("audio_tts_ms"),
                    "meta": meta,
                    "part_types": part_types,
                    "user_chars": len(msg),
                    "assistant_chars": len(reply),
                    "user_preview": msg[:300],
                    "assistant_preview": reply[:300],
                }
            )
            second = {
                "ok": True,
                "partial": False,
                "parts": parts,
                "session_id": sid,
                "meta": meta,
            }
            yield (json.dumps(second, ensure_ascii=False) + "\n").encode("utf-8")

        return StreamingResponse(
            ndjson_audio(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    _append_jsonl(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "chat_ok",
            "session_id": sid,
            "client_ip": client_ip,
            "duration_ms": dt_ms,
            "meta_action": meta.get("action"),
            "ai_markers": meta.get("ai_markers"),
            "ai_response_chars": meta.get("ai_response_chars"),
            "audio_tts_ms": meta.get("audio_tts_ms"),
            "meta": meta,
            "part_types": part_types,
            "user_chars": len(msg),
            "assistant_chars": len(reply),
            "user_preview": msg[:300],
            "assistant_preview": reply[:300],
        }
    )
    return {"ok": True, "reply": reply, "parts": parts, "session_id": sid, "meta": meta}


class WebChatBody(BaseModel):
    token: str = Field(min_length=1)
    message: str = Field(default="", max_length=12000)
    session_id: str = Field(default="", max_length=128)
    image_base64: str | None = Field(default=None, description="Imagen opcional (base64) para editar con Gemini")
    image_mime: str = Field(default="image/jpeg", max_length=64)
    # Compatibilidad: validar clave sin ruta /verify (despliegues antiguos / proxies).
    verify_only: bool = Field(default=False, description="Si true, solo comprueba el token y no llama a la IA")


class WebVoiceBody(BaseModel):
    """Audio grabado en el navegador (WebM/Opus típico) → respuesta MP3 vía Gemini Live."""

    token: str = Field(min_length=1)
    session_id: str = Field(default="", max_length=128)
    audio_base64: str = Field(min_length=1)
    audio_mime: str = Field(default="audio/webm", max_length=128)


class WebVerifyBody(BaseModel):
    token: str = Field(min_length=1)


def _client_ip(request: Request) -> str:
    client = request.client.host if request.client else "?"
    xf = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if xf:
        client = xf.split(",", 1)[0].strip()
    return client


def build_router() -> APIRouter:
    router = APIRouter(tags=["jarvis-web"])

    @router.get("/jarvis-chat", response_class=HTMLResponse)
    def jarvis_chat_page() -> HTMLResponse:
        html_path = BASE_DIR / "jarvis_web_chat.html"
        if not html_path.is_file():
            return HTMLResponse(
                "<h3>Chat web no disponible (falta jarvis_web_chat.html).</h3>",
                status_code=404,
            )
        return HTMLResponse(html_path.read_text(encoding="utf-8", errors="ignore"))

    def _log_verify(event: str, *, client: str, ok: bool) -> None:
        _append_jsonl(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "client_ip": client,
                "ok": ok,
            }
        )

    @router.get("/api/jarvis-web/verify")
    async def jarvis_web_verify_get(request: Request, token: str = Query(..., min_length=1)) -> dict:
        """Comprueba la clave (GET). Útil si POST falla detrás de un proxy; no se registra el valor del token."""
        client = _client_ip(request)
        if not verify_web_chat_token(token):
            _log_verify("verify_get", client=client, ok=False)
            raise HTTPException(status_code=403, detail="Clave incorrecta")
        _log_verify("verify_get", client=client, ok=True)
        return {"ok": True}

    @router.post("/api/jarvis-web/verify")
    async def jarvis_web_verify_post(request: Request, body: WebVerifyBody) -> dict:
        """Comprueba la clave (POST)."""
        client = _client_ip(request)
        if not verify_web_chat_token(body.token):
            _log_verify("verify_post", client=client, ok=False)
            raise HTTPException(status_code=403, detail="Clave incorrecta")
        _log_verify("verify_post", client=client, ok=True)
        return {"ok": True}

    @router.post("/api/jarvis-web/chat", response_model=None)
    async def jarvis_web_chat_api(request: Request, body: WebChatBody):
        if not verify_web_chat_token(body.token):
            raise HTTPException(status_code=403, detail="Token incorrecto")
        client = _client_ip(request)
        if body.verify_only:
            _log_verify("verify_chat_body", client=client, ok=True)
            sid = (body.session_id or "").strip() or "anon"
            return {
                "ok": True,
                "reply": "",
                "parts": [],
                "session_id": sid,
                "meta": {"verify_only": True},
            }
        return await run_web_chat_turn(
            token=body.token,
            message=body.message,
            session_id=body.session_id,
            client_ip=client,
            image_base64=body.image_base64,
            image_mime=body.image_mime or "image/jpeg",
        )

    @router.post("/api/jarvis-web/voice-live")
    async def jarvis_web_voice_live(request: Request, body: WebVoiceBody) -> dict[str, Any]:
        """
        Voz con Gemini Live: el cliente envía un clip (WebM/Opus u otro) y recibe MP3 con la respuesta hablada.
        Mismo backend que Telegram (/cambiargemini), adaptado al chat web.
        """
        if not verify_web_chat_token(body.token):
            raise HTTPException(status_code=403, detail="Token incorrecto")
        client = _client_ip(request)
        try:
            raw = base64.b64decode(body.audio_base64, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="audio_base64 inválido") from None
        if len(raw) < 256:
            raise HTTPException(status_code=400, detail="audio demasiado corto")
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="audio demasiado grande")

        try:
            from jarvis_web_voice import gemini_live_voice_web_mp3
        except ImportError as e:
            logger.exception("jarvis_web_voice import")
            raise HTTPException(status_code=503, detail=f"Módulo de voz no disponible: {e}") from e

        mp3, err = await gemini_live_voice_web_mp3(raw, body.audio_mime or "audio/webm")
        sid = (body.session_id or "").strip() or "anon"
        _append_jsonl(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "voice_live",
                "session_id": sid,
                "client_ip": client,
                "ok": err is None,
                "bytes_in": len(raw),
                "bytes_out": len(mp3) if mp3 else 0,
            }
        )
        if err:
            return {"ok": False, "detail": err, "audio_base64": None, "mime": None}
        return {
            "ok": True,
            "audio_base64": base64.b64encode(mp3).decode("ascii"),
            "mime": "audio/mpeg",
            "session_id": sid,
        }

    return router
