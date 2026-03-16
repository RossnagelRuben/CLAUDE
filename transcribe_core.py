"""
Núcleo de transcripción con faster_whisper. Sin dependencias del bot.
Usado por jarvis_bot (in-process) y por transcription_api (servicio aparte).
Un solo lock por proceso para que el modelo no se use en paralelo.
"""
import threading

_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


def transcribe_voice(audio_path: str) -> str:
    """
    Transcribe un archivo de audio (OGG/MP3) a texto.
    Thread-safe: solo una transcripción a la vez por proceso.
    """
    model = _get_whisper_model()
    with _whisper_lock:
        segments, info = model.transcribe(audio_path, language=None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments if s.text).strip()
    return text or "(sin voz detectada)"
