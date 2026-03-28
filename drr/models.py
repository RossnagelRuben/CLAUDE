# Modelo de dominio para producto (DRR). Una sola responsabilidad: representar datos.

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Any


def bytes_from_drr_imagen_reference(ref: str) -> bytes | None:
    """
    Decodifica imagen si la API envía data: URI o solo base64 (sin prefijo).
    No descarga por HTTP; ver load_drr_product_image_bytes.
    """
    s = (ref or "").strip()
    if not s:
        return None
    if s.startswith("data:") and "base64," in s:
        try:
            b64 = s.split("base64,", 1)[1].strip()
            return base64.b64decode(b64, validate=False)
        except Exception:
            return None
    # Blob base64 típico de APIs .NET (sin data:). Los JPEG en base64 suelen empezar con "/9j/";
    # no excluir "/" aquí o nunca se decodifican (se confundirían con ruta relativa, pero las
    # rutas reales tienen "." u otros chars fuera del alfabeto base64).
    if len(s) >= 80 and not s.startswith(("http://", "https://", "data:")):
        compact = re.sub(r"\s+", "", s)
        if len(compact) >= 80 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
            try:
                return base64.b64decode(compact, validate=False)
            except Exception:
                pass
    return None


def _looks_like_base64_only(s: str) -> bool:
    compact = re.sub(r"\s+", "", s)
    return len(compact) >= 80 and bool(re.fullmatch(r"[A-Za-z0-9+/=]+", compact))


def _absolute_imagen_ref(s: str, base_url: str | None) -> str:
    if s.startswith(("http://", "https://", "data:")):
        return s
    if s.startswith("//"):
        return "https:" + s
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return s
    if s.startswith("/"):
        return f"{base}{s}"
    return f"{base}/{s.lstrip('/')}"


def _imagen_string_from_dict(data: dict[str, Any]) -> str | None:
    for key in (
        "imagenWeb",
        "ImagenWeb",
        "imagenUrl",
        "imagen_url",
        "imagen",
        "Imagen",
        "urlImagen",
        "UrlImagen",
    ):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    img_obj = data.get("imagen") or data.get("Imagen")
    if isinstance(img_obj, dict):
        for key in (
            "imagenWeb",
            "ImagenWeb",
            "imagenUrl",
            "url",
            "Url",
            "base64",
            "Base64",
            "data",
            "contenido",
            "Contenido",
        ):
            s = img_obj.get(key)
            if isinstance(s, str) and s.strip():
                return s.strip()
    return None


def _presentacion_id_de_dict(pr: dict[str, Any]) -> int | None:
    """ID de presentación; soporta presentacionID=0 (no usar `or` entre claves)."""
    for key in ("presentacionID", "PresentacionID"):
        if key not in pr:
            continue
        raw = pr[key]
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _default_presentacion_venta_id(data: dict[str, Any]) -> int | None:
    for key in ("defaultPresentacionVentaID", "DefaultPresentacionVentaID"):
        if key not in data:
            continue
        raw = data[key]
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _presentaciones_ordenadas(data: dict[str, Any]) -> list[dict[str, Any]]:
    pres = data.get("presentaciones") or data.get("Presentaciones") or []
    if not isinstance(pres, list):
        return []
    out = [p for p in pres if isinstance(p, dict)]

    def _pid(pr: dict[str, Any]) -> int:
        i = _presentacion_id_de_dict(pr)
        return i if i is not None else 999

    return sorted(out, key=_pid)


def _imagen_string_from_presentaciones(data: dict[str, Any]) -> str | None:
    """Imagen en presentaciones (DRR suele mandarla ahí con Include=1/2, no en la raíz)."""
    for pr in _presentaciones_ordenadas(data):
        ref = _imagen_string_from_dict(pr)
        if ref:
            return ref
    return None


def _codigo_barras_desde_presentaciones(data: dict[str, Any]) -> str:
    pres = data.get("presentaciones") or data.get("Presentaciones") or []
    if not isinstance(pres, list):
        return ""
    for p in pres:
        if not isinstance(p, dict):
            continue
        lst = p.get("listaCodigoBarra") or p.get("ListaCodigoBarra") or []
        if not isinstance(lst, list):
            continue
        for item in lst:
            if not isinstance(item, dict):
                continue
            cb = (item.get("codigoBarra") or item.get("CodigoBarra") or "").strip()
            if cb:
                return cb
    return ""


def _lista_precio_tuples_from_presentacion(p: dict[str, Any]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    lista = p.get("listaPrecio") or p.get("ListaPrecio") or []
    if not isinstance(lista, list):
        return out
    for lp in lista:
        if not isinstance(lp, dict):
            continue
        lid = lp.get("listaPrecID")
        if lid is None:
            lid = lp.get("ListaPrecID")
        try:
            lid_i = int(lid) if lid is not None else -1
        except (TypeError, ValueError):
            lid_i = -1
        pf = lp.get("precioFinal")
        if pf is None:
            pf = lp.get("PrecioFinal")
        pr = lp.get("precio")
        if pr is None:
            pr = lp.get("Precio")
        val = pf if pf is not None else pr
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            try:
                f = float(str(val).replace(",", ".").strip())
            except (TypeError, ValueError):
                continue
        if f > 0:
            out.append((lid_i, f))
    return out


_PRESENTACION_ETIQUETA_FIJA: dict[int, str] = {0: "Bulto", 1: "Unidad"}


def _nombre_presentacion_para_mostrar(pr: dict[str, Any], pid_i: int | None) -> str:
    if pid_i is not None and pid_i in _PRESENTACION_ETIQUETA_FIJA:
        return _PRESENTACION_ETIQUETA_FIJA[pid_i]
    for k in ("descripcion", "Descripcion", "descripcionCorta", "DescripcionCorta", "nombre", "Nombre"):
        v = pr.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if pid_i is not None:
        return f"Presentación {pid_i}"
    return "Presentación"


def _lista_precio_final_tuples_from_presentacion(p: dict[str, Any]) -> list[tuple[int, float]]:
    """Solo precioFinal / PrecioFinal (sin caer en precio plano). Incluye 0."""
    out: list[tuple[int, float]] = []
    lista = p.get("listaPrecio") or p.get("ListaPrecio") or []
    if not isinstance(lista, list):
        return out
    for lp in lista:
        if not isinstance(lp, dict):
            continue
        lid = lp.get("listaPrecID")
        if lid is None:
            lid = lp.get("ListaPrecID")
        try:
            lid_i = int(lid) if lid is not None else -1
        except (TypeError, ValueError):
            lid_i = -1
        if lid_i < 0:
            continue
        pf = lp.get("precioFinal")
        if pf is None:
            pf = lp.get("PrecioFinal")
        if pf is None:
            continue
        try:
            f = float(pf)
        except (TypeError, ValueError):
            try:
                f = float(str(pf).replace(",", ".").strip())
            except (TypeError, ValueError):
                continue
        if f >= 0:
            out.append((lid_i, f))
    return out


def _elegir_precio_lista(candidates: list[tuple[int, float]], prefer_id: int) -> float | None:
    if not candidates:
        return None
    for lid_i, f in candidates:
        if lid_i == prefer_id:
            return f
    for lid_i, f in candidates:
        if lid_i == 0:
            return f
    return candidates[0][1]


def _precio_desde_presentaciones(data: dict[str, Any]) -> float | None:
    """
    GetProducto anida precios en presentaciones[].listaPrecio[] (precio / precioFinal, listaPrecID).
    Prioriza la presentación de venta por defecto (defaultPresentacionVentaID) si viene en el JSON.
    """
    prefer_raw = os.getenv("DRR_LISTA_PRECIO_ID", "0").strip()
    try:
        prefer_id = int(prefer_raw)
    except ValueError:
        prefer_id = 0

    pres = data.get("presentaciones") or data.get("Presentaciones") or []
    if not isinstance(pres, list):
        return None

    def_pres = data.get("defaultPresentacionVentaID")
    if def_pres is None:
        def_pres = data.get("DefaultPresentacionVentaID")
    try:
        def_pres_i = int(def_pres) if def_pres is not None else None
    except (TypeError, ValueError):
        def_pres_i = None

    candidatos_default: list[tuple[int, float]] = []
    candidatos_todos: list[tuple[int, float]] = []
    for p in pres:
        if not isinstance(p, dict):
            continue
        pid = p.get("presentacionID")
        if pid is None:
            pid = p.get("PresentacionID")
        try:
            pid_i = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_i = None
        tuples = _lista_precio_tuples_from_presentacion(p)
        candidatos_todos.extend(tuples)
        if def_pres_i is not None and pid_i == def_pres_i:
            candidatos_default.extend(tuples)

    picked = _elegir_precio_lista(candidatos_default, prefer_id)
    if picked is not None:
        return picked
    return _elegir_precio_lista(candidatos_todos, prefer_id)


def load_drr_product_image_bytes(ref: str) -> bytes | None:
    """Bytes de imagen de producto DRR: inline/base64 o descarga HTTP(S)."""
    inline = bytes_from_drr_imagen_reference(ref)
    if inline:
        return inline
    s = (ref or "").strip()
    if not s or not s.startswith(("http://", "https://")):
        return None
    try:
        from drr.web_image_search import download_image_url

        return download_image_url(s, max_bytes=20 * 1024 * 1024)
    except Exception:
        return None


@dataclass
class Producto:
    """Representa un producto tal como lo devuelve la API DRR (o mapeado desde JSON)."""

    id: int | str
    codigo_barras: str
    descripcion: str
    imagen_url: str | None = None
    precio: float | None = None
    stock: int | None = None
    # Permite datos extra si la API devuelve más campos (extensible sin romper).
    extra: dict[str, Any] | None = None

    def precios_por_presentacion_final(
        self,
        *,
        solo_lista_id: int | None = None,
    ) -> list[tuple[str, list[tuple[int, float]]]]:
        """
        Por cada presentación (Bulto=0, Unidad=1, etc.): lista de (listaPrecID, precioFinal).
        Orden: presentacionID ascendente.
        """
        data = self.extra or {}
        rows: list[tuple[str, list[tuple[int, float]]]] = []
        for pr in _presentaciones_ordenadas(data):
            pid_i = _presentacion_id_de_dict(pr)
            label = _nombre_presentacion_para_mostrar(pr, pid_i)
            tuples = _lista_precio_final_tuples_from_presentacion(pr)
            if solo_lista_id is not None:
                tuples = [(lid, v) for lid, v in tuples if lid == solo_lista_id]
            tuples.sort(key=lambda x: x[0])
            if tuples:
                rows.append((label, tuples))
        return rows

    def precios_finales_por_lista(self, *, solo_lista_id: int | None = None) -> list[tuple[int, float]]:
        """
        (listaPrecID, precioFinal) desde presentaciones[].listaPrecio[].
        Prioriza la presentación defaultPresentacionVentaID; si no hay datos, usa todas las presentaciones.
        """
        data = self.extra or {}
        pres = data.get("presentaciones") or data.get("Presentaciones") or []
        if not isinstance(pres, list):
            pres = []

        def_pres_i = _default_presentacion_venta_id(data)

        merged: dict[int, float] = {}

        def consume(pr: dict[str, Any]) -> None:
            for lid_i, f in _lista_precio_final_tuples_from_presentacion(pr):
                merged[lid_i] = f

        if def_pres_i is not None:
            for pr in pres:
                if not isinstance(pr, dict):
                    continue
                pid_i = _presentacion_id_de_dict(pr)
                if pid_i == def_pres_i:
                    consume(pr)

        if not merged:
            for pr in pres:
                if isinstance(pr, dict):
                    consume(pr)

        items = sorted(merged.items(), key=lambda x: x[0])
        if solo_lista_id is not None:
            return [(lid, v) for lid, v in items if lid == solo_lista_id]
        return items

    def to_snapshot(self) -> dict[str, Any]:
        """Para listados recientes (imagen por índice en WhatsApp/Telegram)."""
        return {
            "id": self.id,
            "codigo_barras": self.codigo_barras,
            "descripcion": self.descripcion,
            "imagen_url": self.imagen_url,
            "precio": self.precio,
        }

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> "Producto":
        """
        Crea un Producto desde el JSON de la API.
        Acepta camelCase (codigoBarras, imagenUrl, descripcionLarga, codigoID) o snake_case.
        Compatible con GET /Producto y con GET /Producto/GetProducto (repo ProductosAPI Blazor).
        """
        def _get(*keys: str) -> Any:
            for k in keys:
                if data.get(k) is not None:
                    return data.get(k)
            return None

        id_val = _get("id", "codigoID", "CodigoID") or 0
        codigo_barras = str(_get("codigoBarras", "codigo_barras", "codigoBarra") or "").strip()
        if not codigo_barras:
            codigo_barras = _codigo_barras_desde_presentaciones(data)

        descripcion = str(_get("descripcion", "descripcionLarga", "DescripcionLarga", "descripcionCorta") or "").strip()

        img_raw = _imagen_string_from_dict(data) or (_imagen_string_from_presentaciones(data) or "")
        imagen_url: str | None = None
        if img_raw:
            base = os.getenv("DRR_API_BASE_URL", "").strip() or None
            if img_raw.startswith("data:") or _looks_like_base64_only(img_raw):
                imagen_url = img_raw
            else:
                imagen_url = _absolute_imagen_ref(img_raw, base)

        precio_raw = _get(
            "precio",
            "Precio",
            "precioLista",
            "PrecioLista",
            "precioVenta",
            "PrecioVenta",
            "precioUnitario",
            "PrecioUnitario",
            "precioConIva",
            "PrecioConIva",
            "precioCosto",
            "PrecioCosto",
        )
        precio_f: float | None = None
        if precio_raw is not None and precio_raw != "":
            try:
                precio_f = float(precio_raw)
            except (TypeError, ValueError):
                try:
                    precio_f = float(str(precio_raw).replace(",", ".").strip())
                except (TypeError, ValueError):
                    precio_f = None
        if precio_f is None:
            precio_f = _precio_desde_presentaciones(data)

        _extra_skip = frozenset({
            "id", "codigoID", "CodigoID", "codigoBarras", "codigo_barras", "codigoBarra",
            "descripcion", "descripcionLarga", "DescripcionLarga", "descripcionCorta",
            "imagenUrl", "imagen_url", "imagen", "imagenWeb", "ImagenWeb", "Imagen",
            "urlImagen", "UrlImagen",
            "precio", "Precio", "precioLista", "PrecioLista", "precioVenta", "PrecioVenta",
            "precioUnitario", "PrecioUnitario", "precioConIva", "PrecioConIva",
            "precioCosto", "PrecioCosto",
            "stock",
        })

        return cls(
            id=id_val,
            codigo_barras=codigo_barras,
            descripcion=descripcion,
            imagen_url=imagen_url,
            precio=precio_f,
            stock=_get("stock", "stock"),
            extra={k: v for k, v in data.items() if k not in _extra_skip},
        )
