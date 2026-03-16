# Log dedicado para el flujo DRR en Telegram (listar, callbacks, errores).
# Principio SRP (SOLID): una única responsabilidad — registrar acciones DRR para diagnóstico.

import logging
from pathlib import Path
from datetime import datetime

# Ruta del log: mismo directorio base que el bot, carpeta logs
_BASE = Path(__file__).resolve().parent.parent
_DRR_LOG_FILE = _BASE / "logs" / "drr_bot.log"

# Logger interno por si queremos también salida a consola
_logger = logging.getLogger("drr.bot")


def _ensure_log_dir() -> None:
    _DRR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def drr_log(action: str, detail: str = "", level: str = "INFO") -> None:
    """
    Escribe una línea en el log dedicado DRR (drr_bot.log).
    Permite revisar y depurar listados, botones y fallos sin mezclar con el log general.
    """
    _ensure_log_dir()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{level}] {action}"
    if detail:
        # Limitar longitud y evitar saltos de línea que rompan formato
        safe = detail.replace("\n", " ").strip()[:500]
        line += f" — {safe}"
    line += "\n"
    try:
        with _DRR_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        _logger.warning("No se pudo escribir en drr_bot.log: %s", e)
    if level == "ERROR":
        _logger.error("%s — %s", action, detail)
