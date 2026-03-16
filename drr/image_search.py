# Búsqueda de imágenes con DuckDuckGo. Implementa IBuscadorImagenes.
# Responsabilidad única: ejecutar búsqueda y devolver lista de resultados con URL de imagen.

import logging
from typing import Any

from drr.interfaces import IBuscadorImagenes

logger = logging.getLogger(__name__)


def _search_images_sync(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Búsqueda síncrona con ddgs.images()."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=max_results))
    except Exception as e:
        logger.warning("DuckDuckGo images search failed: %s", e)
        return []
    # Normalizar: ddgs devuelve 'image' (URL) y a veces 'thumbnail', 'url', 'title'
    out = []
    for r in results:
        url = r.get("image") or r.get("url") or r.get("thumbnail")
        if url:
            out.append({"url": url, "image": url, "title": r.get("title") or ""})
    return out[:max_results]


class DuckDuckGoBuscadorImagenes(IBuscadorImagenes):
    """Implementación de búsqueda de imágenes usando DuckDuckGo (ddgs)."""

    def buscar(self, query: str, max_results: int = 5) -> list[dict]:
        return _search_images_sync(query.strip(), max_results=max_results)
