"""Búsqueda de imágenes en la web (DuckDuckGo vía ddgs) con reintentos y descarga robusta."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def download_image_url(url: str, *, max_bytes: int = 20 * 1024 * 1024) -> bytes | None:
    """Descarga bytes de una URL de imagen (User-Agent de navegador; muchos CDN bloquean urllib simple)."""
    try:
        import httpx

        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        }
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.content
            if data and len(data) <= max_bytes:
                return data
    except Exception as e:
        logger.warning("download_image_url %s: %s", (url or "")[:90], e)
    return None


def _ddgs_image_results(query: str, *, max_results: int, timeout: int) -> list[dict]:
    """Varias regiones y reintentos: DDG a veces devuelve 403 o vqd inválido según región."""
    from ddgs import DDGS

    regions = ["wt-wt", "us-en", "es-es", "ar-es", "uk-en", "de-de"]
    last_err: Exception | None = None
    for region in regions:
        try:
            with DDGS(timeout=timeout) as ddgs:
                return list(
                    ddgs.images(
                        query.strip(),
                        max_results=max_results,
                        region=region,
                        safesearch="moderate",
                    )
                )
        except Exception as e:
            last_err = e
            logger.info("DDGS images query=%r region=%s: %s", query[:60], region, e)
            time.sleep(0.35)
    if last_err:
        logger.warning("DDGS images sin resultados tras regiones: %s", last_err)
    return []


def search_web_image_bytes(query: str, *, max_size: int = 20 * 1024 * 1024) -> tuple[bytes | None, str]:
    """
    Busca con DuckDuckGo (ddgs), descarga la primera imagen válida.
    Devuelve (bytes, mime) o (None, "").
    """
    q = (query or "").strip()
    if not q:
        return None, ""

    results = _ddgs_image_results(q, max_results=12, timeout=25)
    for r in results:
        u = r.get("image") or r.get("url") or r.get("thumbnail")
        if not u:
            continue
        img_bytes = download_image_url(u, max_bytes=max_size)
        if not img_bytes:
            continue
        mime = "image/jpeg"
        lu = u.lower()
        if lu.endswith(".png"):
            mime = "image/png"
        elif lu.endswith(".webp"):
            mime = "image/webp"
        return img_bytes, mime
    return None, ""
