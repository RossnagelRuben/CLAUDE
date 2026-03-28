# Catálogo de listas de precio DRR (GET /Empresa/ListaPrecio). Caché en memoria.

from __future__ import annotations

import os

from drr.api_client import DRRProductoAPIClient
from drr.cache import TTLCache

_cache = TTLCache[str, dict[int, str]](
    ttl_seconds=int(os.getenv("DRR_LISTA_PRECIO_CACHE_SEG", "3600")),
    max_items=32,
)


def nombres_listas_precio(base_url: str, api_key: str | None) -> dict[int, str]:
    """Mapa listaPrecID → descripción (con caché)."""
    bu = (base_url or "").strip().rstrip("/")
    if not bu:
        return {}
    hit = _cache.get(bu)
    if hit is not None:
        return hit
    client = DRRProductoAPIClient(bu, api_key=api_key, cache_ttl_seconds=25)
    m = client.catalogo_listas_precio()
    _cache.set(bu, m)
    return m
