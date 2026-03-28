"""
Registro persistente para depurar panel WhatsApp / Evolution (QR, logout, proxies).

Archivo por defecto: ``logs/whatsapp_debug.log`` (junto al proyecto).
Desactivar: ``WHATSAPP_DEBUG_LOG=0`` en ``.env``.
Ruta custom: ``WHATSAPP_DEBUG_LOG_PATH=/var/log/jarvis_whatsapp.log``
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parent
_LOCK = threading.Lock()
# Siempre en memoria para ver desde el panel web sin SSH (últimos N eventos).
_RING_MAX = int(os.getenv("WHATSAPP_DEBUG_RING_MAX", "500") or "500")
_RING_MAX = max(50, min(_RING_MAX, 2000))
_RING: deque[dict[str, Any]] = deque(maxlen=_RING_MAX)

_ENABLED = os.getenv("WHATSAPP_DEBUG_LOG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "",
)


def debug_log_enabled() -> bool:
    return _ENABLED
_RAW_PATH = (os.getenv("WHATSAPP_DEBUG_LOG_PATH") or "").strip()
LOG_FILE: Path = Path(_RAW_PATH) if _RAW_PATH else (_BASE / "logs" / "whatsapp_debug.log")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def redact_query(url_path: str) -> str:
    """Oculta token= y claves sensibles en query string (solo para logs)."""
    if "?" not in url_path:
        return url_path
    path, q = url_path.split("?", 1)
    parts = []
    for pair in q.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, _ = pair.split("=", 1)
            lk = k.lower()
            if lk in ("token", "secret", "apikey", "key", "password"):
                parts.append(f"{k}=[REDACTED]")
            else:
                parts.append(pair)
        else:
            parts.append(pair)
    return path + "?" + "&".join(parts)


def truncate_blob(s: str, max_len: int = 400) -> str:
    """Acorta JSON/cuerpos con base64 largo."""
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"... [truncado, total {len(s)} chars]"


def redact_jsonish(s: str) -> str:
    """Quita o acorta campos que suelen ser imágenes base64."""
    if not s or len(s) < 80:
        return s
    # data:image o bloques base64 muy largos
    out = re.sub(
        r"(data:image/[^;]+;base64,)([A-Za-z0-9+/=]{60,})",
        r"\1[BASE64_REDACTED]",
        s,
        flags=re.DOTALL,
    )
    out = re.sub(
        r'"base64"\s*:\s*"([^"]{120,})"',
        '"base64":"[BASE64_REDACTED]"',
        out,
    )
    return truncate_blob(out, 2000)


def log_event(category: str, data: dict[str, Any]) -> None:
    row = {"ts": _utc_iso(), "category": category, **data}
    with _LOCK:
        _RING.append(row)
    if not _ENABLED:
        return
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


def get_recent_debug_events(limit: int = 80) -> list[dict[str, Any]]:
    """Últimos eventos, más recientes primero (para el panel web)."""
    limit = max(1, min(int(limit or 80), 500))
    with _LOCK:
        items = list(_RING)
    # el más nuevo está al final del deque
    tail = items[-limit:]
    return list(reversed(tail))


def log_http_request(
    *,
    method: str,
    path: str,
    client_host: str,
    status_code: int,
    duration_ms: float,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = {
        "method": method,
        "path": redact_query(path),
        "client": client_host,
        "status": status_code,
        "duration_ms": round(duration_ms, 2),
    }
    if extra:
        payload.update(extra)
    log_event("http", payload)
