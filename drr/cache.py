"""
Caché simple con TTL (time-to-live) para respuestas HTTP.

Motivación:
- Evitar sobrecargar DRR APIs en búsquedas repetidas (por voz o texto).
- Mantener una implementación pequeña y testeable (SOLID: una sola responsabilidad).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class _Entry(Generic[V]):
    value: V
    expires_at: float


class TTLCache(Generic[K, V]):
    """Caché en memoria con TTL y tamaño máximo (evicción FIFO simple)."""

    def __init__(self, ttl_seconds: int = 30, max_items: int = 200):
        self.ttl_seconds = int(ttl_seconds)
        self.max_items = int(max_items)
        self._store: dict[K, _Entry[V]] = {}
        self._order: list[K] = []

    def get(self, key: K) -> V | None:
        now = time.time()
        entry = self._store.get(key)
        if not entry:
            return None
        if entry.expires_at <= now:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: K, value: V) -> None:
        now = time.time()
        expires_at = now + self.ttl_seconds
        if key not in self._store:
            self._order.append(key)
        self._store[key] = _Entry(value=value, expires_at=expires_at)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while len(self._order) > self.max_items:
            k = self._order.pop(0)
            self._store.pop(k, None)

