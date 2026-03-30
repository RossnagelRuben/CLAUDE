# Caché en memoria con TTL simple.

from __future__ import annotations

import threading
import time
from typing import Any


class TtlCache:
    """Thread-safe get/set con expiración."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            exp, val = item
            if exp < now:
                del self._data[key]
                return None
            return val

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        exp = time.monotonic() + max(0.1, ttl_seconds)
        with self._lock:
            self._data[key] = (exp, value)
