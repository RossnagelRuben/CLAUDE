"""
Integración con el catálogo de comercio de Meta (WhatsApp Business / Commerce).

Los productos que ves en el catálogo de WhatsApp Business viven en un **catálogo de Meta**
vinculado a tu cuenta. Evolution API no crea ítems en ese catálogo; hay que usar la
**Graph API (Marketing API)** con un token que tenga permisos sobre el catálogo.

Variables de entorno (definir en `.env` del bridge):

- ``META_ACCESS_TOKEN`` — token de usuario de sistema o de usuario con ``catalog_management``
  y acceso al catálogo (según cómo armes la app en developers.facebook.com).
- ``META_PRODUCT_CATALOG_ID`` — ID numérico del catálogo (Commerce Manager → catálogo → ID).
- ``META_GRAPH_API_VERSION`` — opcional, por defecto ``v21.0``.
- ``META_CATALOG_CURRENCY`` — opcional, código ISO de moneda del precio (ej. ``ARS``, ``USD``).
- ``META_CATALOG_DEFAULT_PRODUCT_LINK`` — opcional, plantilla de URL del producto. Placeholders:
  ``{id}`` (codigoID DRR), ``{codigo_barras}``. Ejemplo::
    https://mitienda.com/producto/{id}

Requisitos del lado de Meta / DRR:

- La **imagen** debe ser una URL **http(s)** pública que los servidores de Meta puedan
  descargar. Si en DRR solo tenés base64 embebido, este módulo no podrá subir el ítem hasta
  que la API exponga una URL o alojes la imagen en un lugar público.

Documentación útil (Meta, inglés):

- `https://developers.facebook.com/docs/marketing-api/reference/product-catalog/batch/`
- `https://developers.facebook.com/docs/whatsapp/cloud-api/guides/sell-products-and-services/`
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Versión de Graph API: alinear con la documentación vigente de tu app.
_DEFAULT_GRAPH_VERSION = "v21.0"


def _graph_version() -> str:
    v = (os.getenv("META_GRAPH_API_VERSION") or _DEFAULT_GRAPH_VERSION).strip().lstrip("/")
    return v if v else _DEFAULT_GRAPH_VERSION


def _access_token() -> str:
    return (os.getenv("META_ACCESS_TOKEN") or "").strip()


def _catalog_id() -> str:
    return (os.getenv("META_PRODUCT_CATALOG_ID") or "").strip()


def _currency() -> str:
    return (os.getenv("META_CATALOG_CURRENCY") or "ARS").strip().upper() or "ARS"


def meta_catalog_upload_configured() -> bool:
    """True si hay token y catálogo configurados (el bridge puede ofrecer el comando al usuario)."""
    return bool(_access_token() and _catalog_id())


def _default_product_link(snapshot: dict[str, Any]) -> str | None:
    """
    URL de detalle del producto para el campo ``url`` del ítem de catálogo.
    Si no hay plantilla en env, se omite (Meta a veces lo exige según vertical).
    """
    tpl = (os.getenv("META_CATALOG_DEFAULT_PRODUCT_LINK") or "").strip()
    if not tpl:
        return None
    sid = str(snapshot.get("id") or "")
    cb = str(snapshot.get("codigo_barras") or "")
    try:
        return tpl.format(id=sid, codigo_barras=cb)
    except (KeyError, ValueError, IndexError):
        return tpl


def _public_image_url_from_snapshot(snapshot: dict[str, Any], drr_base_url: str) -> tuple[str | None, str | None]:
    """
    Devuelve (url_https, None) o (None, mensaje de error en español).
    Meta requiere ``image_url`` accesible públicamente por HTTPS (salvo flujos avanzados).
    """
    ref = snapshot.get("imagen_url")
    if not ref or not isinstance(ref, str):
        return None, (
            "Este producto no tiene imagen en el snapshot de DRR. "
            "Cargá una imagen en DRR o pedí la foto y guardala antes de subir al catálogo."
        )
    ref = ref.strip()
    if ref.startswith("https://"):
        return ref, None
    if ref.startswith("http://"):
        return ref, None
    # Ruta relativa: absolutar con base de la API DRR (debe ser alcanzable desde internet).
    base = (drr_base_url or "").strip().rstrip("/")
    if ref.startswith("/") and base.startswith(("http://", "https://")):
        return f"{base}{ref}", None
    if not ref.startswith("/") and base.startswith(("http://", "https://")):
        return f"{base}/{ref.lstrip('/')}", None
    # Base64 / data URI: Meta no acepta eso directamente como image_url.
    if ref.startswith("data:") or _looks_like_base64_blob(ref):
        return None, (
            "La imagen del producto está en base64 o data: URI; Meta necesita una URL https "
            "pública. Publicá la imagen en DRR con URL o usá un hosting accesible."
        )
    return None, (
        "No pude obtener una URL https pública para la imagen. "
        "Revisá que la API DRR devuelva imagenWeb/imagen con URL accesible."
    )


def _looks_like_base64_blob(s: str) -> bool:
    compact = re.sub(r"\s+", "", s)
    return len(compact) >= 80 and bool(re.fullmatch(r"[A-Za-z0-9+/=]+", compact))


def _format_price(snapshot: dict[str, Any]) -> str:
    """
    Meta suele aceptar precio como ``\"1234.56 ARS\"`` en el catálogo por lotes (feed-style).
    Si no hay precio en el snapshot, usamos 0.01 + moneda para no enviar vacío (podés ajustar
    política según tu negocio).
    """
    cur = _currency()
    raw = snapshot.get("precio")
    if raw is None or raw == "":
        return f"0.01 {cur}"
    try:
        p = float(raw)
    except (TypeError, ValueError):
        try:
            p = float(str(raw).replace(",", ".").strip())
        except (TypeError, ValueError):
            return f"0.01 {cur}"
    return f"{p:.2f} {cur}"


def _retailer_id(snapshot: dict[str, Any]) -> str:
    """SKU estable para Meta: preferimos código de barras; si no, codigoID DRR."""
    cb = str(snapshot.get("codigo_barras") or "").strip()
    if cb:
        return f"drr-{cb}"[:200]
    sid = str(snapshot.get("id") or "").strip()
    return f"drr-id-{sid}"[:200] if sid else "drr-unknown"


def upload_product_from_snapshot(
    snapshot: dict[str, Any],
    *,
    drr_base_url: str,
    timeout_s: float = 60.0,
) -> tuple[bool, str]:
    """
    Crea un producto en el catálogo de Meta vía ``POST /{catalog_id}/items_batch`` (recomendado).

    Returns:
        (True, mensaje de éxito) o (False, mensaje de error legible).
    """
    if not meta_catalog_upload_configured():
        return False, (
            "Falta configurar META_ACCESS_TOKEN y META_PRODUCT_CATALOG_ID en el entorno del bridge."
        )

    img_url, err_img = _public_image_url_from_snapshot(snapshot, drr_base_url)
    if err_img or not img_url:
        return False, err_img or "Imagen inválida."

    token = _access_token()
    cid = _catalog_id()
    name = str(snapshot.get("descripcion") or "Producto").strip() or "Producto"
    name = name[:200]
    description = str(snapshot.get("descripcion") or "")[:4999]

    link = _default_product_link(snapshot)
    price_str = _format_price(snapshot)
    rid = _retailer_id(snapshot)

    # ``items_batch`` es el endpoint recomendado por Meta (sustituye al legacy ``/batch``).
    # El ``id`` del ítem va *dentro* de ``data``; antes iba como ``retailer_id`` suelto.
    # Nombres de campo: ``title``, ``image_link``, ``link`` (ver guía de migración).
    # Docs: https://developers.facebook.com/docs/marketing-api/catalog/guides/manage-catalog-items/catalog-batch-api/migrate-to-items-batch
    data_obj: dict[str, Any] = {
        "id": rid,
        "title": name,
        "description": description,
        "image_link": img_url,
        "price": price_str,
        "availability": "in stock",
        "condition": "new",
    }
    if link:
        data_obj["link"] = link[:2000]

    batch_body = [
        {
            "method": "CREATE",
            "data": data_obj,
        }
    ]

    graph = f"https://graph.facebook.com/{_graph_version()}/{cid}/items_batch"
    form = {
        "access_token": token,
        "item_type": "PRODUCT_ITEM",
        "requests": json.dumps(batch_body, ensure_ascii=False),
        "allow_upsert": "true",
    }

    try:
        r = httpx.post(graph, data=form, timeout=timeout_s)
    except httpx.HTTPError as e:
        logger.exception("meta_catalog: error HTTP")
        return False, f"Error de red al llamar a Meta: {e!s}"

    try:
        payload = r.json()
    except Exception:
        return False, f"Meta respondió sin JSON (HTTP {r.status_code}): {r.text[:500]}"

    if r.status_code >= 400:
        err = payload.get("error", {})
        msg = err.get("message") or payload.get("message") or r.text[:800]
        logger.warning("meta_catalog: HTTP %s error=%s body=%s", r.status_code, msg, payload)
        return False, f"Meta (HTTP {r.status_code}): {msg}"

    # Respuesta típica: { "handles": [...], "validation_status": [ { "retailer_id", "errors", "warnings" } ] }
    vs = payload.get("validation_status")
    if isinstance(vs, list) and vs:
        first = vs[0]
        errs = first.get("errors") if isinstance(first, dict) else None
        if isinstance(errs, list) and errs:
            e0 = errs[0]
            if isinstance(e0, dict):
                em = e0.get("message") or str(e0)
            else:
                em = str(e0)
            return False, f"Meta rechazó el ítem: {em}"
        warnings = first.get("warnings") if isinstance(first, dict) else None
        wrn_txt = ""
        if isinstance(warnings, list) and warnings:
            w0 = warnings[0]
            if isinstance(w0, dict):
                wrn_txt = f" (avisos: {w0.get('message', warnings)})"
            else:
                wrn_txt = f" (avisos: {warnings})"
        return True, f"Producto enviado al catálogo Meta (retailer_id={rid}).{wrn_txt}"

    handles = payload.get("handles")
    if handles:
        return True, f"Solicitud registrada en Meta (handles={handles!r}). Revisá el catálogo en Commerce Manager."

    return True, "Respuesta OK de Meta (sin detalle de validación). Revisá el catálogo en Commerce Manager."
