"""
Voz de respuesta con Gemini Live a partir de un archivo de audio (OGG/MP3/WAV…).
Usado por Telegram, WhatsApp y pruebas; evita duplicar lógica.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _pcm24_to_mp3(pcm: bytes) -> bytes | None:
    if not pcm:
        return None
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-i",
                "pipe:0",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                "-f",
                "mp3",
                "pipe:1",
            ],
            input=pcm,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("gemini_voice_shared: pcm→mp3: %s", e)
        return None


async def gemini_live_reply_mp3_from_path(audio_path: str) -> bytes | None:
    """
    Archivo de audio del usuario → respuesta hablada en MP3 (Gemini Live).
    """
    from jarvis_bot import (
        GEMINI_API_KEY,
        _gemini_live_voice_response,
        _ogg_to_pcm_16k,
    )

    if not (GEMINI_API_KEY or "").strip():
        return None
    p = Path(audio_path)
    if not p.is_file():
        return None
    pcm16 = await asyncio.to_thread(_ogg_to_pcm_16k, None, p)
    if not pcm16:
        try:
            raw = p.read_bytes()
            pcm16 = await asyncio.to_thread(_ogg_to_pcm_16k, raw, None)
        except OSError:
            pcm16 = None
    if not pcm16:
        return None
    sys_txt = (
        "Sos Jarvis. Respondé en español, claro y breve. "
        "Si piden acciones del servidor, indicá que pueden escribirlo y confirmar."
    )
    try:
        pcm24 = await asyncio.wait_for(
            _gemini_live_voice_response(pcm16, sys_txt),
            timeout=float(os.getenv("GEMINI_LIVE_TIMEOUT_SEC", "90")),
        )
    except asyncio.TimeoutError:
        logger.warning("gemini_voice_shared: timeout Gemini Live")
        return None
    if not pcm24:
        return None
    return await asyncio.to_thread(_pcm24_to_mp3, pcm24)


async def gemini_live_reply_ogg_from_path(audio_path: str) -> bytes | None:
    """Igual que MP3 pero OGG Opus para Telegram reply_voice."""
    from jarvis_bot import (
        GEMINI_API_KEY,
        _gemini_live_voice_response,
        _ogg_to_pcm_16k,
        _pcm_24k_to_ogg,
    )

    if not (GEMINI_API_KEY or "").strip():
        return None
    p = Path(audio_path)
    if not p.is_file():
        return None
    pcm16 = await asyncio.to_thread(_ogg_to_pcm_16k, None, p)
    if not pcm16:
        try:
            pcm16 = await asyncio.to_thread(_ogg_to_pcm_16k, p.read_bytes(), None)
        except OSError:
            pcm16 = None
    if not pcm16:
        return None
    sys_txt = (
        "Sos Jarvis, asistente de voz del servidor. Respondé siempre en español, breve y claro. "
        "Si el usuario pide hacer algo en el servidor, decile que puede escribirlo por texto y confirmar con /confirm."
    )
    try:
        pcm24 = await asyncio.wait_for(
            _gemini_live_voice_response(pcm16, sys_txt),
            timeout=float(os.getenv("GEMINI_LIVE_TIMEOUT_SEC", "90")),
        )
    except asyncio.TimeoutError:
        return None
    if not pcm24:
        return None
    return await asyncio.to_thread(_pcm_24k_to_ogg, pcm24)


def wa_gemini_voice_enabled() -> bool:
    """Por defecto activo; desactivar con JARVIS_DISABLE_WA_GEMINI_VOICE=1."""
    v = os.getenv("JARVIS_DISABLE_WA_GEMINI_VOICE", "").strip().lower()
    return v not in ("1", "true", "yes", "si", "sí")
