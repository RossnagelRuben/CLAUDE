"""
API de transcripción de voz (FastAPI). Pensada para usarse en un proceso aparte
y/o desde N8N. El bot puede llamar a POST /transcribe para no bloquear y ganar velocidad.
Principio SRP: este servicio solo expone transcripción vía HTTP.

Timeouts: la transcripción se ejecuta en thread con límite de tiempo para no colgar
la petición si el audio es muy largo o el modelo tarda.
"""
import asyncio
import logging
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

# Reutilizar la misma lógica que el bot (faster_whisper)
from transcribe_core import transcribe_voice

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Transcripción de voz", version="1.0")

# Máximo tiempo que puede tardar la transcripción; evita que una petición quede colgada para siempre.
TRANSCRIBE_REQUEST_TIMEOUT = 120


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """
    Recibe un archivo de audio (OGG/MP3) y devuelve el texto transcrito.
    Respuesta: {"text": "..."}. Timeout interno para no colgar si el servidor tarda.
    """
    suffix = Path(audio.filename or "audio.ogg").suffix or ".ogg"
    if suffix.lower() not in (".ogg", ".oga", ".mp3", ".wav", ".m4a"):
        suffix = ".ogg"
    try:
        body = await audio.read()
    except Exception as e:
        logger.warning("Error leyendo archivo: %s", e)
        raise HTTPException(status_code=400, detail="No se pudo leer el archivo")
    if len(body) > 25 * 1024 * 1024:  # 25 MB
        raise HTTPException(status_code=400, detail="Archivo demasiado grande (máx 25 MB)")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(body)
        path = f.name
    start = time.perf_counter()
    try:
        # Ejecutar en thread con timeout: si Whisper tarda más, respondemos error en lugar de colgar.
        text = await asyncio.wait_for(
            asyncio.to_thread(transcribe_voice, path),
            timeout=TRANSCRIBE_REQUEST_TIMEOUT,
        )
        duration = time.perf_counter() - start
        logger.info("Transcripción OK en %.2fs, %d bytes", duration, len(body))
        return JSONResponse(content={"text": text or "(sin voz detectada)"})
    except asyncio.TimeoutError:
        duration = time.perf_counter() - start
        logger.warning("Transcripción timeout tras %.1fs (límite %ds)", duration, TRANSCRIBE_REQUEST_TIMEOUT)
        raise HTTPException(status_code=504, detail=f"Transcripción superó el tiempo límite ({TRANSCRIBE_REQUEST_TIMEOUT}s)")
    except Exception as e:
        logger.exception("Error transcribiendo: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])
    finally:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/health")
async def health():
    return {"status": "ok"}
