"""
Voz en el chat web con Gemini Live (misma API que Telegram: entrada PCM 16 kHz → salida PCM 24 kHz).

No es WebSocket full-duplex: es «mantener pulsado / soltar», enviar un clip y recibir audio de respuesta.
Requiere GEMINI_API_KEY, ffmpeg y google-genai con soporte live.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Máx. ~90 s de audio webm razonable
_MAX_VOICE_BYTES = 8 * 1024 * 1024


def _ffmpeg_bytes_to_pcm16k_mono(audio_bytes: bytes, suffix: str) -> bytes | None:
    """Decodifica casi cualquier formato (webm, ogg, wav, mp4) a PCM s16le 16 kHz mono."""
    if not audio_bytes:
        return None
    tmp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(suffix=suffix, prefix="jarvis_web_voice_")
        os.close(fd)
        tmp = Path(name)
        tmp.write_bytes(audio_bytes)
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp),
                "-f",
                "s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-",
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            logger.warning(
                "ffmpeg decode voz web: rc=%s err=%s",
                proc.returncode,
                (proc.stderr or b"")[:400].decode(errors="replace"),
            )
            return None
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("ffmpeg decode voz web: %s", e)
        return None
    finally:
        if tmp and tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def _pcm24k_to_mp3(pcm_bytes: bytes) -> bytes | None:
    """PCM s16le 24 kHz mono → MP3 para el reproductor del navegador."""
    if not pcm_bytes:
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
            input=pcm_bytes,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            logger.warning(
                "ffmpeg pcm→mp3: rc=%s",
                proc.returncode,
            )
            return None
        return proc.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("ffmpeg pcm→mp3: %s", e)
        return None


def _suffix_for_mime(mime: str) -> str:
    m = (mime or "").split(";")[0].strip().lower()
    if "webm" in m:
        return ".webm"
    if "ogg" in m or "opus" in m:
        return ".ogg"
    if "wav" in m:
        return ".wav"
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "mp4" in m or "m4a" in m:
        return ".m4a"
    return ".webm"


async def gemini_live_voice_web_mp3(audio_bytes: bytes, mime: str) -> tuple[bytes | None, str | None]:
    """
    Devuelve (mp3_bytes, error). error es None si OK.
    """
    if len(audio_bytes) > _MAX_VOICE_BYTES:
        return None, "El audio es demasiado largo (máx. ~1–2 minutos)."
    suf = _suffix_for_mime(mime)
    pcm16 = await asyncio.to_thread(_ffmpeg_bytes_to_pcm16k_mono, audio_bytes, suf)
    if not pcm16:
        return None, "No se pudo decodificar el audio (¿ffmpeg instalado?)."

    from jarvis_bot import GEMINI_API_KEY, _gemini_live_voice_response

    if not (GEMINI_API_KEY or "").strip():
        return None, "GEMINI_API_KEY no configurada en el servidor."

    sys_instr = (
        "Sos Jarvis en el chat web por voz. Respondé en español, claro y breve. "
        "Si piden acciones del servidor, deciles que por texto pueden usar comandos confirmados."
    )
    pcm24 = await _gemini_live_voice_response(pcm16, sys_instr)
    if not pcm24:
        return None, "Gemini Live no devolvió audio (revisá modelo GEMINI_LIVE_VOICE_MODEL o cuota API)."

    mp3 = await asyncio.to_thread(_pcm24k_to_mp3, pcm24)
    if not mp3:
        return None, "No se pudo convertir la respuesta a MP3."
    return mp3, None
