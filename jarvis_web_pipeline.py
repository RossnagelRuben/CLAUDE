"""
Pipeline Jarvis para el chat web — paridad de capacidades con _process_user_message (Telegram).

Cada sesión web tiene su propio user_data (no se usan pending_command / pending_code globales de Telegram).
Las respuestas se devuelven como lista de «parts» (texto, imagen, audio, archivo) para que la API JSON
las serialice (p. ej. base64) y el navegador las muestre.

Ver también: jarvis_bot._process_user_message, agent_data/logs/jarvis_web_chat.jsonl
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Any

from drr.chat_intents import parse_edit_image_intent, parse_producto_imagen_index
from jarvis_text_display import strip_markdown_display_symbols

logger = logging.getLogger(__name__)

# Refuerzo solo en chat web: la IA suele usar IMAGEN:/AUDIO: sin pedido explícito si no se recalca.
_WEB_CHAT_SYSTEM_SUFFIX = (
    "\n\n--- [Chat web — reglas estrictas] ---\n"
    "Respondé en TEXTO corrido en español en la mayoría de los casos.\n"
    "Usá la línea IMAGEN: solo si el usuario pidió explícitamente generar/crear/dibujar una imagen.\n"
    "NUNCA uses la línea AUDIO: salvo que en ESTE mensaje el usuario pida explícitamente que GENERES o CREES un audio "
    "(o use el comando /audio). Frases vagas («en voz», «leeme») NO cuentan: sin pedido explícito de generar audio, SOLO TEXTO.\n"
    "Preguntas tipo «qué podés hacer», «qué hora es», ayuda o charla normal: SOLO TEXTO, sin AUDIO:.\n"
    "No uses BUSCAR: ni BUSCAR_IMAGEN: salvo que pida buscar en internet o una imagen en la web.\n"
    "Para charla, preguntas, ayuda o saludos: solo texto, sin esas marcas.\n"
    "---\n"
)

# Sesión: el usuario pidió no recibir más TTS hasta que vuelva a pedir audio explícitamente.
_WEB_AUDIO_OPT_OUT_KEY = "web_audio_opt_out"


def _web_msg_refuses_audio(msg: str) -> bool:
    """Usuario pide respuesta solo por texto / sin audio."""
    m = (msg or "").strip()
    if not m:
        return False
    return bool(
        re.search(
            r"(?is)(no\s+quiero\s+audio|sin\s+audio|solo\s+texto|respond(é|e)me\s+por\s+texto|"
            r"no\s+me\s+mand(es|á)\s+audio|basta\s+de\s+audio|par(a|á)\s+con\s+el\s+audio|"
            r"dej(a|á)\s+de\s+.*audio|texto\s+sol(o|amente)|"
            r"no\s+quiero\s+voz|sin\s+voz|solo\s+escrito|por\s+texto\s+nom[aá]s)",
            m,
        )
    )


def _web_msg_requests_audio_output(msg: str) -> bool:
    """
    True solo si el usuario pide explícitamente generar/crear/hacer un audio (o /audio).
    No alcanza con «en voz», «léeme», etc.
    """
    m = (msg or "").strip()
    if not m:
        return False
    if re.search(r"(?i)(^|[\s,.;])\/audio\b", m):
        return True
    return bool(
        re.search(
            r"(?is)\b(gener(á|a|ar|ame|eme|es|enos|en)|cre(ar|á|ame)|hac(e|é|er|eme))\s+(un\s+)?(el\s+)?(audio|tts|nota de voz|mensaje de voz)\b",
            m,
        )
        or re.search(
            r"(?is)\b(generar|crear|hacer)\s+.{0,35}(un\s+)?(el\s+)?(audio|tts|nota de voz)\b",
            m,
        )
        or re.search(
            r"(?is)\bque\s+(generes|generés|crees|hagas)\s+(un\s+)?(el\s+)?(audio|tts)\b",
            m,
        )
        or re.search(
            r"(?is)\b(pedí|pedi|quiero)\s+que\s+(generes|crees|hagas)\s+.{0,20}(un\s+)?audio\b",
            m,
        )
    )


def _web_update_audio_opt_out(ud: dict[str, Any], msg: str) -> None:
    """Actualiza preferencia de sesión según el mensaje del usuario."""
    if _web_msg_refuses_audio(msg):
        ud[_WEB_AUDIO_OPT_OUT_KEY] = True
    elif _web_msg_requests_audio_output(msg):
        ud[_WEB_AUDIO_OPT_OUT_KEY] = False


def _response_markers_summary(text: str) -> str:
    """Para logs / JSONL: qué marcadores parece tener la respuesta del modelo (sin guardar el contenido)."""
    if not text or not text.strip():
        return "empty"
    t = text[:8000]
    tags: list[str] = []
    if re.search(r"(?is)IMAGEN\s*:", t):
        tags.append("IMAGEN")
    if re.search(r"(?is)AUDIO\s*:", t):
        tags.append("AUDIO")
    if re.search(r"(?is)BUSCAR_IMAGEN\s*:", t):
        tags.append("BUSCAR_IMAGEN")
    if re.search(r"(?is)BUSCAR\s*:", t):
        tags.append("BUSCAR")
    if re.search(r"(?is)CMD\s*:", t) or re.search(r"(?is)ACCION\s*:", t):
        tags.append("CMD")
    if "CALENDAR_PROPUESTA:" in t:
        tags.append("CALENDAR")
    if re.search(r"(?is)NOTA\s*:", t):
        tags.append("NOTA")
    return "+".join(tags) if tags else "text_only"


# Sesión web → estado (mismo espíritu que context.user_data en Telegram)
_WEB_UD: dict[str, dict[str, Any]] = {}

# Límite de sesiones en memoria (evita crecimiento indefinido)
_MAX_WEB_SESSIONS = 600


def _web_uid(session_id: str) -> str:
    """ID estable para servicios que esperan user_id (cripto, archivos temporales)."""
    s = (session_id or "anon").strip()[:128]
    return f"web_{s}"


def _get_ud(session_id: str) -> dict[str, Any]:
    if session_id not in _WEB_UD:
        _WEB_UD[session_id] = {}
    return _WEB_UD[session_id]


def _trim_web_sessions() -> None:
    if len(_WEB_UD) <= _MAX_WEB_SESSIONS:
        return
    drop = len(_WEB_UD) - int(_MAX_WEB_SESSIONS * 0.85)
    for i, key in enumerate(list(_WEB_UD.keys())):
        if i >= drop:
            break
        _WEB_UD.pop(key, None)


def _normalize_for_ai_markers(text: str) -> str:
    """Unicode (p. ej. ： en lugar de :) rompe los regex; normalizamos para detectar IMAGEN:/BUSCAR:."""
    if not text:
        return ""
    t = text.replace("\u3000", " ").replace("：", ":")
    return t


def _strip_outer_markdown_fence(text: str) -> str:
    """
    Algunos modelos envuelven toda la respuesta en ``` … ```; sin esto no se detecta IMAGEN:/NOTA:.
    """
    t = (text or "").strip()
    if not t.startswith("```"):
        return text or ""
    lines = t.split("\n")
    if len(lines) < 2:
        return text or ""
    body = "\n".join(lines[1:])
    body = body.rstrip()
    if body.endswith("```"):
        body = body[:-3].rstrip()
    return body


def _extract_imagen_prompt(response: str) -> str | None:
    """
    Detecta el payload tras IMAGEN: aunque venga como «IMAGEN :» (espacio antes de dos puntos),
    dos puntos unicode 「：」, guión en lugar de «:», o con **markdown** alrededor.
    """
    raw = _normalize_for_ai_markers(_strip_outer_markdown_fence(response))
    patterns = (
        r"(?is)IMAGEN\s*:\s*(.+)",
        r"(?is)IMAGEN\s*[-–—]\s*(.+)",
        r"(?is)(?:^|\n)\s*\*{0,2}\s*IMAGEN\s*:\s*(.+)",
        r"(?is)(?:^|\n)\s*Imagen\s*:\s*(.+)",
        r"(?is)(?:^|\n)\s*Imagen\s*[-–—]\s*(.+)",
    )
    for pat in patterns:
        m = re.search(pat, raw, re.DOTALL)
        if m:
            p = (m.group(1) or "").strip()
            if p:
                return p
    return None


def _imagen_false_positive_prompt(prompt: str) -> bool:
    """Evita confundir ayuda del sistema con un prompt real de imagen."""
    p = prompt or ""
    return any(
        x in p
        for x in (
            "AUDIO:",
            "BUSCAR:",
            "CMD:",
            "NOTA:",
            "Logs",
            "Proyectos",
        )
    )


def _part_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": strip_markdown_display_symbols(text or "")}


def _part_image(mime: str, image_bytes: bytes, caption: str = "") -> dict[str, Any]:
    return {
        "type": "image",
        "mime": mime or "image/png",
        "base64": base64.b64encode(image_bytes).decode("ascii"),
        "caption": (caption or "")[:500],
    }


def _part_audio(mime: str, audio_bytes: bytes) -> dict[str, Any]:
    return {
        "type": "audio",
        "mime": mime or "audio/mpeg",
        "base64": base64.b64encode(audio_bytes).decode("ascii"),
    }


def _part_file(filename: str, mime: str, raw_bytes: bytes) -> dict[str, Any]:
    return {
        "type": "file",
        "filename": filename,
        "mime": mime,
        "base64": base64.b64encode(raw_bytes).decode("ascii"),
    }


def _assistant_history_text(parts: list[dict[str, Any]]) -> str:
    """Texto compacto para el historial conversacional (sin base64)."""
    chunks: list[str] = []
    for p in parts:
        t = p.get("type")
        if t == "text":
            chunks.append(p.get("text") or "")
        elif t == "image":
            chunks.append("[Imagen]")
        elif t == "audio":
            chunks.append("[Audio]")
        elif t == "file":
            chunks.append(f"[Archivo: {p.get('filename', 'adjunto')}]")
    return "\n".join(x for x in chunks if x).strip() or "(respuesta multimedia)"


async def _try_confirm_or_cancel_web(
    session_id: str, text: str, parts: list[dict[str, Any]]
) -> bool:
    """
    Si el mensaje confirma o cancela un CMD pendiente, ejecuta y devuelve True (flujo terminado).
    """
    from jarvis_bot import append_log

    ud = _get_ud(session_id)
    raw = text.strip()
    low = raw.lower()

    if low in ("/cancel", "cancelar", "cancel"):
        if ud.get("pending_cmd") is not None:
            ud.pop("pending_cmd", None)
            ud.pop("pending_code", None)
            parts.append(_part_text("✅ Acción pendiente cancelada."))
            append_log("assistant", "Web: comando pendiente cancelado", entry_type="comando")
            return True
        return False

    m = re.match(r"^/confirm\s+(\d{4})\s*$", raw, re.I)
    if not m:
        m = re.match(r"^(?:confirmar|confirm)\s+(\d{4})\s*$", raw, re.I)
    if not m:
        return False

    code = m.group(1).strip()
    pending_cmd = ud.get("pending_cmd")
    pending_code = ud.get("pending_code")
    if pending_cmd is None:
        parts.append(_part_text("No hay ninguna acción pendiente."))
        return True
    if code != pending_code:
        parts.append(_part_text("❌ Código incorrecto."))
        return True

    cmd = pending_cmd
    ud.pop("pending_cmd", None)
    ud.pop("pending_code", None)

    parts.append(_part_text(f"✅ Ejecutando:\n{cmd}"))

    try:
        from jarvis_bot import get_recent_logs_for_context, get_ai_response
        from server_executor import default_executor

        r = default_executor.run(cmd, timeout_seconds=120)
        append_log(
            "sistema",
            f"Comando ejecutado (web): {cmd}\nResultado:\n{r.output[:2000]}",
            entry_type="comando",
        )
        reply = "📄 Resultado del comando:\n\n" + r.output
        if not r.success and r.hint:
            reply += "\n\n💡 " + r.hint
        parts.append(_part_text(reply[:12000]))
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
            parts.append(_part_text("🧠 Explicación del comando:\n\n" + explicacion[:4000]))
        except Exception as e2:
            logger.warning("Explicación IA post-comando web: %s", e2)
    except Exception as e:
        append_log("sistema", f"Error ejecutando (web) {cmd}: {e}", entry_type="error")
        parts.append(_part_text(f"❌ Error ejecutando comando: {e}"))
    return True


async def _handle_crypto_web(
    session_id: str, text: str, parts: list[dict[str, Any]]
) -> bool:
    from crypto.commands import try_handle_crypto_command
    from jarvis_bot import _get_crypto_service, append_log

    uid = _web_uid(session_id)
    out = try_handle_crypto_command(text, uid, _get_crypto_service())
    if out is None:
        return False
    # Respuesta larga en trozos lógicos para la UI
    chunk = 4096
    for i in range(0, len(out), chunk):
        parts.append(_part_text(out[i : i + chunk]))
    append_log("user", text.strip())
    append_log("assistant", out[:1500])

    svc = _get_crypto_service()
    prep = svc.peek_last_prepared(uid)
    if prep and "🧾 Swap preparado" in out:
        raw_b64 = prep.swap_transaction_base64.encode("ascii")
        parts.append(
            _part_file("jupiter_swap_tx.b64.txt", "text/plain", raw_b64)
        )
        parts.append(
            _part_text("📎 TX base64 adjunta para firmar en Phantom / Solflare.")
        )
    return True


def _store_last_image(ud: dict[str, Any], image_bytes: bytes, mime: str) -> None:
    from jarvis_bot import LAST_IMAGE_FOR_EDIT_KEY

    ud[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": image_bytes, "mime_type": mime or "image/jpeg"}


async def _generate_image_web(ud: dict[str, Any], prompt: str, parts: list[dict[str, Any]]) -> None:
    from jarvis_bot import GEMINI_API_KEY, OPENAI_API_KEY, LAST_IMAGE_FOR_EDIT_KEY, append_log
    from drr.image_generate_env import generate_image_bytes_env

    if not GEMINI_API_KEY and not OPENAI_API_KEY:
        parts.append(_part_text("No hay API key de imagen configurada (GEMINI_API_KEY u OPENAI_API_KEY)."))
        return
    prompt = prompt.strip()[:1000]
    if not prompt:
        return
    parts.append(_part_text("🖼 Generando imagen…"))
    logger.info("jarvis_web_pipeline: image_gen_begin prompt_chars=%s", len(prompt))
    image_bytes = generate_image_bytes_env(prompt)
    if image_bytes:
        append_log("sistema", f"Imagen (web): {prompt[:80]}...", entry_type="imagen")
        parts.append(_part_image("image/png", image_bytes, prompt[:200]))
        _store_last_image(ud, image_bytes, "image/png")
    else:
        parts.append(_part_text("❌ No se pudo generar la imagen."))


async def _edit_image_web(ud: dict[str, Any], prompt: str, parts: list[dict[str, Any]]) -> None:
    from jarvis_bot import GEMINI_API_KEY, LAST_IMAGE_FOR_EDIT_KEY, append_log
    from drr.gemini_image_edit import gemini_edit_image_bytes

    if not GEMINI_API_KEY:
        parts.append(_part_text("Para editar imágenes necesito GEMINI_API_KEY en el servidor."))
        return
    data = ud.get(LAST_IMAGE_FOR_EDIT_KEY)
    if not data or not data.get("bytes"):
        parts.append(
            _part_text(
                "No tengo ninguna imagen para editar. Subí una imagen en el chat o pedí una generada antes."
            )
        )
        return
    prompt = prompt.strip()[:1000]
    if not prompt:
        parts.append(_part_text("Escribí qué cambio querés (ej: cambia el fondo a una playa)."))
        return
    parts.append(_part_text("🖼 Editando imagen con Gemini…"))
    try:
        img_bytes = data["bytes"]
        mime = data.get("mime_type") or "image/png"
        out_bytes = gemini_edit_image_bytes(
            api_key=GEMINI_API_KEY,
            image_bytes=img_bytes,
            mime_type=mime,
            prompt=prompt,
        )
        if out_bytes:
            parts.append(_part_image("image/png", out_bytes, prompt[:200]))
            ud[LAST_IMAGE_FOR_EDIT_KEY] = {"bytes": out_bytes, "mime_type": "image/png"}
            append_log("sistema", f"Imagen editada web (Gemini): {prompt[:80]}...", entry_type="imagen")
        else:
            parts.append(_part_text("❌ No se obtuvo imagen editada."))
    except Exception as e:
        logger.exception("edit_image_web: %s", e)
        parts.append(_part_text(f"❌ No pude editar la imagen: {e}"))


async def complete_web_tts(
    session_id: str,
    parts: list[dict[str, Any]],
    texto_audio: str,
    meta: dict[str, Any],
) -> None:
    """
    Genera el MP3 después de que el cliente ya recibió «Generando audio…» (respuesta NDJSON en dos fases).
    """
    texto_audio = (texto_audio or "").strip()
    if not texto_audio:
        meta.pop("tts_pending", None)
        return
    t_tts = time.perf_counter()
    logger.info(
        "jarvis_web_pipeline: edge_tts_begin session=%s chars=%s",
        session_id,
        len(texto_audio),
    )
    await _generate_audio_web(texto_audio, parts)
    meta["audio_tts_ms"] = int((time.perf_counter() - t_tts) * 1000)
    meta.pop("tts_pending", None)
    logger.info(
        "jarvis_web_pipeline: edge_tts_end session=%s ms=%s",
        session_id,
        meta.get("audio_tts_ms"),
    )


async def _generate_audio_web(text_audio: str, parts: list[dict[str, Any]]) -> None:
    """
    Genera MP3 con edge-tts. El mensaje «Generando audio…» lo agrega el caller **antes** de llamar acá
    para que en la UI el texto de estado quede siempre encima del reproductor.
    """
    from jarvis_bot import VOICE_TEMP_DIR, append_log

    text_audio = text_audio.strip()[:2000]
    if not text_audio:
        return
    tmp_path = VOICE_TEMP_DIR / f"tts_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000,9999)}.mp3"
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text_audio, voice="es-AR-ElenaNeural")
        await communicate.save(str(tmp_path))
        raw = tmp_path.read_bytes()
        parts.append(_part_audio("audio/mpeg", raw))
        append_log("sistema", f"Audio (web): {text_audio[:80]}...", entry_type="audio")
    except Exception as e:
        logger.exception("TTS web: %s", e)
        parts.append(_part_text(f"❌ Error al generar audio: {e}"))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


async def process_web_message(
    session_id: str,
    message: str,
    *,
    combined_for_ai: str | None = None,
    client_ip: str = "?",
    image_bytes: bytes | None = None,
    image_mime: str = "image/jpeg",
) -> dict[str, Any]:
    """
    Procesa un turno completo tipo Telegram.

    - ``message``: texto del usuario (para regex, logs, prefs DRR).
    - ``combined_for_ai``: opcional; si viene, es lo que se envía al modelo (p. ej. historial + mensaje).
    - ``image_bytes``: si el cliente subió imagen, se guarda como última imagen editable (como foto en Telegram).
    """
    import google_workspace

    from jarvis_bot import (
        DRR_API_BASE_URL,
        DRR_API_KEY,
        GEMINI_API_KEY,
        LAST_IMAGE_FOR_EDIT_KEY,
        MAX_IMAGE_EDIT_BYTES,
        USE_GEMINI_VOICE_KEY,
        append_log,
        get_ai_response,
        get_recent_logs_for_context,
        save_note,
        _build_drr_zero_log,
        _get_productos,
        _get_servicio_productos,
        _parse_product_prefs_from_user_text,
        _search_web_image_and_download,
        _sort_products_by_last_modified,
        _telegram_drive_resolve_path,
        search_web,
    )
    from drr.api_client import DRRProductoAPIClient, fetch_product_image_bytes_for_snapshot

    sid = (session_id or "anon").strip()[:128]
    ud = _get_ud(sid)
    parts: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"pipeline": "web", "client_ip": client_ip}

    msg = (message or "").strip()
    _web_update_audio_opt_out(ud, msg)
    user_for_ai = (combined_for_ai if combined_for_ai is not None else msg).strip()
    user_for_ai_model = user_for_ai + _WEB_CHAT_SYSTEM_SUFFIX

    # --- Subida de imagen (paridad con foto en Telegram) ---
    if image_bytes:
        if len(image_bytes) > MAX_IMAGE_EDIT_BYTES:
            parts.append(
                _part_text(
                    f"La imagen es muy pesada (máx {MAX_IMAGE_EDIT_BYTES // (1024 * 1024)} MB). Probá con otra más chica."
                )
            )
        else:
            _store_last_image(ud, image_bytes, image_mime or "image/jpeg")
            parts.append(
                _part_text(
                    "✅ Imagen guardada para editar.\n\n"
                    "Escribí cómo querés editarla (ej: «cambia el fondo a una playa») o pedí generar otra imagen antes."
                )
            )
        meta["had_image_upload"] = True
        if not msg:
            meta["action"] = "image_only_ack"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    if not msg and not parts:
        meta["action"] = "empty"
        return {"ok": True, "parts": [_part_text("(vacío)")], "session_id": sid, "meta": meta}

    # Sin texto útil pero ya respondimos por imagen
    if not msg and parts:
        meta["action"] = "image_only_ack"
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- /confirm y /cancel (comandos servidor por sesión) ---
    if await _try_confirm_or_cancel_web(sid, msg, parts):
        meta["action"] = "confirm_cancel"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Cripto ---
    if await _handle_crypto_web(sid, msg, parts):
        meta["action"] = "crypto"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Calendario: respuesta SI/NO a propuesta previa ---
    pending_cal = ud.get("pending_calendar_event")
    if pending_cal:
        tcal = msg.upper()
        if tcal in ("SI", "SÍ", "S", "YES", "Y"):
            ud.pop("pending_calendar_event", None)
            try:
                pl = google_workspace.normalize_calendar_payload(pending_cal)
                ev = google_workspace.create_calendar_event(
                    pl["title"],
                    pl["start"],
                    end_iso=pl.get("end"),
                    description=pl.get("description"),
                )
                link = (ev.get("htmlLink") or "").strip()
                out = "✅ Evento creado en Google Calendar." + (f"\n{link}" if link else "")
                parts.append(_part_text(out))
                append_log("assistant", out, entry_type="message")
            except Exception as e:
                logger.exception("Web calendar create")
                parts.append(_part_text(f"❌ No se pudo crear el evento: {e}"))
            meta["action"] = "calendar_confirm"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        if tcal in ("NO", "N", "CANCELAR"):
            ud.pop("pending_calendar_event", None)
            parts.append(_part_text("Ok, no lo agendé en el calendario."))
            meta["action"] = "calendar_deny"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        ud.pop("pending_calendar_event", None)

    # --- Opción voz Gemini (misma convención que Telegram; en web solo informa estado) ---
    t_lower = msg.lower()
    if re.search(r"cambiar(s)?\s+a\s+gemini|usar\s+gemini\s+para\s+voz|gemini\s+para\s+voz", t_lower):
        ud[USE_GEMINI_VOICE_KEY] = True
        if not GEMINI_API_KEY:
            ud[USE_GEMINI_VOICE_KEY] = False
            parts.append(
                _part_text("❌ No está configurada GEMINI_API_KEY. En el chat web la voz nativa no aplica igual que en Telegram.")
            )
        else:
            parts.append(
                _part_text(
                    "✅ Preferencia «voz Gemini» anotada en esta sesión. "
                    "El chat web no reproduce voz como Telegram; seguís con texto aquí."
                )
            )
        append_log("user", msg)
        append_log("assistant", "Web: usuario tocó preferencia voz Gemini.")
        meta["action"] = "gemini_voice_toggle"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
    if re.search(r"volver\s+a\s+claude|cambiar(s)?\s+a\s+claude|desactivar\s+gemini\s+voz", t_lower):
        ud[USE_GEMINI_VOICE_KEY] = False
        parts.append(_part_text("✅ Volviste al modo normal (preferencia de sesión web)."))
        append_log("user", msg)
        meta["action"] = "gemini_voice_off"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Imagen de producto DRR por lenguaje natural ---
    idx_drr = parse_producto_imagen_index(msg)
    if idx_drr is not None and ud.get("drr_last_products_snap"):
        snaps = ud["drr_last_products_snap"]
        if not (1 <= idx_drr <= len(snaps)):
            parts.append(
                _part_text(
                    f"En el último listado solo hay {len(snaps)} producto(s). Pedí un número entre 1 y {len(snaps)}."
                )
            )
            meta["action"] = "drr_image_index_error"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        row = snaps[idx_drr - 1]
        if not DRR_API_BASE_URL:
            parts.append(_part_text("❌ DRR no está configurado (DRR_API_BASE_URL)."))
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        parts.append(_part_text("🖼 Descargando imagen del producto…"))
        img_bytes = await asyncio.to_thread(
            fetch_product_image_bytes_for_snapshot,
            row,
            base_url=DRR_API_BASE_URL,
            api_key=DRR_API_KEY or None,
        )
        if img_bytes:
            desc = (row.get("descripcion") or "")[:100]
            cap = f"Producto {idx_drr}: {desc}" if desc else f"Producto {idx_drr}"
            _store_last_image(ud, img_bytes, "image/jpeg")
            parts.append(_part_image("image/jpeg", img_bytes, cap[:200]))
            parts.append(
                _part_text("✅ Podés pedirme que la edite describiendo el cambio (ej. «edita esta imagen: …»).")
            )
            meta["action"] = "drr_product_image"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        parts.append(
            _part_text(
                "No pude obtener la imagen (reintento con Include=2 / Observaciones en la API DRR)."
            )
        )
        meta["action"] = "drr_product_image_fail"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Edición Gemini ---
    edit_prompt = parse_edit_image_intent(msg)
    if edit_prompt and ud.get(LAST_IMAGE_FOR_EDIT_KEY):
        await _edit_image_web(ud, edit_prompt, parts)
        meta["action"] = "edit_image"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Búsqueda rápida «producto(s) …» sin IA ---
    m_prod_quick = re.match(r"^(producto|productos)\s+(.+)$", msg, flags=re.IGNORECASE)
    if m_prod_quick:
        servicio = _get_servicio_productos()
        if not servicio:
            parts.append(
                _part_text(
                    "❌ DRR no configurado. Añadí DRR_API_BASE_URL en el .env del servidor."
                )
            )
            meta["action"] = "drr_quick_fail"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        query = m_prod_quick.group(2).strip()
        parts.append(_part_text("📋 Buscando productos…"))
        try:

            def _listar():
                return servicio.listar(descripcion=query, codigo_barras=None, limit=10)

            listado, lista = await asyncio.to_thread(_listar)
            if len(lista) == 0:
                log_para_copiar = _build_drr_zero_log(servicio, descripcion=query, codigo_barras=None)
                ud["drr_last_zero_log"] = log_para_copiar
                parts.append(_part_text(listado[:4000] + "\n\n_(Log técnico disponible en sesión para soporte.)_"))
            else:
                parts.append(_part_text(listado[:4000]))
        except Exception as e:
            logger.exception("Web búsqueda producto: %s", e)
            parts.append(_part_text(f"❌ Error buscando productos: {e}"))
        meta["action"] = "drr_quick"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- Log usuario + IA con memoria de logs diarios (como Telegram) ---
    append_log("user", msg, entry_type="message")
    context_memory = get_recent_logs_for_context(days=3, max_chars=3500)

    try:
        logger.info(
            "jarvis_web_pipeline: get_ai_response start session=%s user_chars=%s",
            sid,
            len(user_for_ai_model),
        )
        response = await get_ai_response(user_for_ai_model, context_memory=context_memory)
    except Exception as e:
        append_log("sistema", f"Error IA web: {e}", entry_type="error")
        logger.exception("get_ai_response web")
        meta["action"] = "ai_error"
        _trim_web_sessions()
        return {
            "ok": False,
            "parts": [_part_text(f"❌ Error al generar respuesta: {e}")],
            "session_id": sid,
            "meta": meta,
            "error": str(e)[:800],
        }

    response = response or ""
    # Desenvuelve ``` si el modelo envolvió toda la respuesta (mejora detección de IMAGEN:/NOTA:/…).
    response = _strip_outer_markdown_fence(response)
    meta["ai_markers"] = _response_markers_summary(response)
    meta["ai_response_chars"] = len(response)
    logger.info(
        "jarvis_web_pipeline: modelo respondió session=%s markers=%s chars=%s preview=%r",
        sid,
        meta["ai_markers"],
        len(response),
        (response[:120] + "…") if len(response) > 120 else response,
    )

    # --- CALENDAR_PROPUESTA ---
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
            parts.append(_part_text(f"❌ Calendario (datos inválidos): {e}"))
            if reply_rest.strip():
                parts.append(_part_text(reply_rest[:4000]))
            meta["action"] = "calendar_bad_payload"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        if not google_workspace.oauth_configured():
            parts.append(
                _part_text("📅 Falta configurar Google OAuth en el servidor. Ver docs/GOOGLE_CALENDAR_DRIVE.md")
            )
            if reply_rest.strip():
                parts.append(_part_text(reply_rest[:4000]))
            meta["action"] = "calendar_oauth_missing"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        if not google_workspace.is_authorized():
            prefix = (os.getenv("JARVIS_PUBLIC_BASE_URL") or "").strip().rstrip("/") or "http://TU_VPS"
            parts.append(
                _part_text(
                    "📅 Conectá Google una vez en el navegador:\n"
                    f"{prefix}/admin/google/oauth/start?token=(ADMIN_PANEL_TOKEN)"
                )
            )
            if reply_rest.strip():
                parts.append(_part_text(reply_rest[:4000]))
            meta["action"] = "calendar_not_linked"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        ud["pending_calendar_event"] = payload
        parts.append(
            _part_text(
                "📅 ¿Lo agendo en tu Google Calendar?\n\n"
                + google_workspace.format_event_for_user(payload)
                + "\n\nRespondé SI para confirmar o NO para cancelar."
            )
        )
        rr = reply_rest.strip()
        if rr.startswith("NOTA:"):
            note_text = rr.replace("NOTA:", "", 1).strip()
            path = save_note(note_text)
            append_log("assistant", f"Nota guardada: {note_text}", entry_type="nota")
            parts.append(_part_text(f"📝 Nota guardada en:\n{path}\n\n{note_text}"))
        elif rr:
            parts.append(_part_text(rr[:4000]))
        meta["action"] = "calendar_proposal"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- DRIVE_SUBIR ---
    dm = re.search(r"(?mi)^DRIVE_SUBIR:\s*(\S+)\s*$", response)
    if dm:
        rel = dm.group(1).strip()
        rest = re.sub(r"(?mi)^DRIVE_SUBIR:\s*\S+\s*", "", response, count=1).strip()
        path = _telegram_drive_resolve_path(rel)
        if not path or not path.is_file():
            parts.append(_part_text(f"❌ No encuentro el archivo: `{rel}`"))
            if rest:
                parts.append(_part_text(rest[:4000]))
            meta["action"] = "drive_upload_missing"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        if not google_workspace.oauth_configured() or not google_workspace.is_authorized():
            prefix = (os.getenv("JARVIS_PUBLIC_BASE_URL") or "").strip().rstrip("/") or "http://TU_VPS"
            parts.append(
                _part_text(
                    f"📁 Conectá Google primero: {prefix}/admin/google/oauth/start?token=(ADMIN_PANEL_TOKEN)"
                )
            )
            if rest:
                parts.append(_part_text(rest[:4000]))
            meta["action"] = "drive_oauth"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        try:
            up = google_workspace.upload_file_to_drive(path, drive_name=path.name)
            link = (up.get("webViewLink") or "").strip()
            msg_up = f"✅ Subido a Drive: {up.get('name')}" + (f"\n{link}" if link else "")
            parts.append(_part_text(msg_up))
        except Exception as e:
            logger.exception("Web Drive upload")
            parts.append(_part_text(f"❌ Error subiendo a Drive: {e}"))
        if rest:
            parts.append(_part_text(rest[:4000]))
        meta["action"] = "drive_upload"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- NOTA simple (respuesta empieza por NOTA:) ---
    if response.startswith("NOTA:"):
        note_text = response.replace("NOTA:", "", 1).strip()
        path = save_note(note_text)
        append_log("assistant", f"Nota guardada: {note_text}", entry_type="nota")
        parts.append(_part_text(f"📝 Nota guardada en:\n{path}\n\n{note_text}"))
        meta["action"] = "note"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- PRODUCTOS DRR ---
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
            parts_q = query.split("|")
            desc = parts_q[0].strip()
            if len(parts_q) > 1 and parts_q[1].strip().isdigit():
                limit = min(int(parts_q[1].strip()), 20)
        except Exception:
            desc = query
            limit = 5

        prefs = _parse_product_prefs_from_user_text(msg)
        final_limit = prefs.get("limit") if prefs.get("limit") is not None else limit
        final_include_prices = prefs.get("include_prices")
        if final_include_prices is None:
            final_include_prices = True
        final_order = prefs.get("order")
        final_solo_lista = prefs.get("solo_lista_precio_id")

        append_log(
            "sistema",
            f"DRR filtros (web): desc={desc!r} limit={final_limit} include_prices={final_include_prices} order={final_order!r} solo_lista={final_solo_lista!r}",
        )
        parts.append(_part_text(f"📦 Buscando productos: {desc or 'todos'} (limit={final_limit})…"))
        resultado = _get_productos(
            descripcion=desc,
            limit=final_limit,
            include_prices=final_include_prices,
            order=final_order,
            solo_lista_precio_id=final_solo_lista,
        )
        append_log("assistant", f"PRODUCTOS => {resultado[:200]}", entry_type="producto")
        try:
            fetch_limit = final_limit if final_order is None else max(final_limit, 50)
            repo = DRRProductoAPIClient(DRR_API_BASE_URL, api_key=DRR_API_KEY or None, cache_ttl_seconds=25)
            plist = repo.listar(descripcion=desc or None, limit=fetch_limit)
            if final_order in ("last_modified_desc", "last_modified_asc"):
                plist = _sort_products_by_last_modified(plist, final_order)
            shown = plist[:final_limit]
            ud["drr_last_products_snap"] = [p.to_snapshot() for p in shown]
        except Exception:
            ud["drr_last_products_snap"] = []
        out_txt = f"📦 Productos DRR:\n{resultado}"
        if ud.get("drr_last_products_snap"):
            out_txt += "\n\n_Para ver la imagen de un ítem: «imagen del producto 1» o «foto del primero»._"
        parts.append(_part_text(out_txt[:12000]))
        meta["action"] = "productos_drr"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- PRODUCTO_IMAGEN: ---
    if "PRODUCTO_IMAGEN:" in response.upper():
        m = re.search(r"PRODUCTO_IMAGEN:\s*(\d+)", response, flags=re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1))
            except Exception:
                n = 0
            if n > 0:
                snaps = ud.get("drr_last_products_snap") or []
                if not snaps:
                    parts.append(
                        _part_text(
                            "No hay listado de productos reciente. Pedime primero productos (ej. «traeme 5 martillos»)."
                        )
                    )
                    meta["action"] = "producto_imagen_no_list"
                    _trim_web_sessions()
                    return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
                if n < 1 or n > len(snaps):
                    parts.append(_part_text(f"En el último listado solo hay {len(snaps)} producto(s)."))
                    _trim_web_sessions()
                    return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
                row = snaps[n - 1]
                if not DRR_API_BASE_URL:
                    parts.append(_part_text("❌ DRR no está configurado (DRR_API_BASE_URL)."))
                    _trim_web_sessions()
                    return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
                parts.append(_part_text("🖼 Descargando imagen del producto…"))
                img_bytes = await asyncio.to_thread(
                    fetch_product_image_bytes_for_snapshot,
                    row,
                    base_url=DRR_API_BASE_URL,
                    api_key=DRR_API_KEY or None,
                )
                if not img_bytes:
                    parts.append(
                        _part_text(
                            "No pude obtener la imagen (reintento con Include=2 / Observaciones en la API DRR)."
                        )
                    )
                    _trim_web_sessions()
                    return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
                desc = (row.get("descripcion") or "")[:100]
                cap = f"Producto {n}: {desc}" if desc else f"Producto {n}"
                _store_last_image(ud, img_bytes, "image/jpeg")
                parts.append(_part_image("image/jpeg", img_bytes, cap[:200]))
                parts.append(
                    _part_text("✅ Podés pedirme que la edite (ej. «edita esta imagen: …»).")
                )
                meta["action"] = "producto_imagen_marker"
                _trim_web_sessions()
                return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- BUSCAR_IMAGEN ---
    m_bimg = re.search(r"(?is)BUSCAR_IMAGEN\s*:\s*(.+)", response, re.DOTALL)
    if m_bimg:
        query_img = m_bimg.group(1).strip().split("\n")[0].strip()
        if query_img:
            parts.append(_part_text("🔍 Buscando imagen en internet…"))
            img_bytes, mime = await asyncio.to_thread(_search_web_image_and_download, query_img)
            if img_bytes:
                _store_last_image(ud, img_bytes, mime or "image/jpeg")
                parts.append(_part_image(mime or "image/jpeg", img_bytes, f"Búsqueda: {query_img[:100]}"))
                parts.append(
                    _part_text(
                        "✅ Imagen lista; pedime cómo editarla o seguí la conversación."
                    )
                )
                append_log(
                    "sistema",
                    f"BUSCAR_IMAGEN web: {query_img[:60]} -> imagen guardada para editar",
                    entry_type="imagen",
                )
            else:
                parts.append(
                    _part_text("No encontré ninguna imagen para esa búsqueda. Probá con otra frase o subí una foto.")
                )
            meta["action"] = "buscar_imagen"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- IMAGEN: (regex tolerante a «IMAGEN :» y variantes; ver _extract_imagen_prompt) ---
    prompt_imagen = _extract_imagen_prompt(response)
    if prompt_imagen is not None:
        if _imagen_false_positive_prompt(prompt_imagen):
            append_log("assistant", response)
            parts.append(_part_text(response[:12000]))
            meta["action"] = "imagen_false_positive"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        append_log("assistant", f"Generando imagen: {prompt_imagen[:100]}...", entry_type="imagen")
        await _generate_image_web(ud, prompt_imagen, parts)
        meta["action"] = "imagen"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- AUDIO: solo TTS si el usuario pidió audio en este mensaje y no se opuso (el modelo a menudo alucina AUDIO:)
    m_aud = re.search(r"(?is)AUDIO\s*:\s*(.+)", response, re.DOTALL)
    if m_aud:
        texto_audio = m_aud.group(1).strip()
        allow_tts = _web_msg_requests_audio_output(msg) and not ud.get(_WEB_AUDIO_OPT_OUT_KEY)
        if not allow_tts:
            append_log("assistant", texto_audio[:800], entry_type="message")
            parts.append(_part_text(texto_audio[:12000]))
            meta["action"] = "audio_model_only_text"
            logger.info(
                "jarvis_web_pipeline: AUDIO del modelo convertido a texto (sin pedido explícito de voz) session=%s",
                sid,
            )
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        parts.append(_part_text("🔊 Generando audio…"))
        append_log("assistant", f"Generando audio: {texto_audio[:80]}...", entry_type="audio")
        await asyncio.sleep(0)
        meta["tts_pending"] = texto_audio
        meta["action"] = "audio"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- BUSCAR: ---
    m_bus = re.search(r"(?is)BUSCAR\s*:\s*(.+)", response, re.DOTALL)
    if m_bus:
        query = m_bus.group(1).strip()
        parts.append(_part_text("🔍 Buscando en internet…"))
        try:
            results_text = await search_web(query)
            append_log("sistema", f"Búsqueda web: {query[:60]}...", entry_type="busqueda")
            prompt_con_resultados = (
                f"El usuario preguntó: {msg.strip()}\n\n"
                f"Se buscó en internet con la consulta: {query}\n\n"
                "Información encontrada:\n"
                f"{results_text}\n\n"
                "Resumí o respondé en español según esta información. Si no hay nada relevante, decilo brevemente."
            )
            respuesta_final = await get_ai_response(prompt_con_resultados, context_memory=None)
            append_log(
                "assistant",
                respuesta_final[:200] + ("..." if len(respuesta_final) > 200 else ""),
            )
            parts.append(_part_text(respuesta_final[:12000]))
        except Exception as e:
            logger.exception("Búsqueda web: %s", e)
            parts.append(_part_text(f"❌ Error al buscar: {e}"))
        meta["action"] = "buscar"
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    # --- CMD: / ACCION: ---
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
        if not cmd and "CMD:" in response:
            m = re.search(r"CMD:\s*(.+?)(?:\n|$)", response, re.DOTALL)
            if m:
                cmd = m.group(1).strip()
        if not cmd:
            append_log("assistant", response)
            parts.append(_part_text(response[:12000]))
            meta["action"] = "cmd_skipped"
            _trim_web_sessions()
            return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
        code = str(random.randint(1000, 9999))
        ud["pending_cmd"] = cmd
        ud["pending_code"] = code
        msg_cmd = (
            f"⚠️ Acción detectada\n\n"
            f"Acción: {accion or 'sin descripción'}\n"
            f"Comando: {cmd}\n\n"
            f"Confirmá escribiendo:\n/confirm {code}\n\n"
            f"(o «confirmar {code}» sin barra)"
        )
        append_log("assistant", f"Propuesta de comando (web): {cmd}", entry_type="comando")
        parts.append(_part_text(msg_cmd))
        meta["action"] = "cmd_pending"
        meta["pending_confirm_hint"] = True
        _trim_web_sessions()
        return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}

    append_log("assistant", response)
    parts.append(_part_text(response[:12000]))
    meta["action"] = "chat_text"
    _trim_web_sessions()
    return {"ok": True, "parts": parts, "session_id": sid, "meta": meta}
