# Formateo de productos para Telegram. Una sola responsabilidad: texto legible y breve.
# Principio SRP (SOLID): este módulo solo se encarga de dar formato a datos de producto.

from __future__ import annotations

import os

from drr.models import Producto


def _imagen_para_texto(url: str | None) -> str:
    if not url:
        return ""
    u = url.strip()
    if u.startswith("data:image"):
        return "sí (imagen en base64 desde la API)"
    if len(u) > 200 and not u.startswith(("http://", "https://", "/")):
        return "sí (base64 desde la API)"
    return f"{u[:80]}…" if len(u) > 80 else u


def _descripcion_lista_precio(lid: int, nombres: dict[int, str]) -> str:
    """Nombre de lista desde catálogo DRR; sin prefijos tipo L0/L1."""
    s = str((nombres or {}).get(lid, "") or "").strip()
    if s:
        return s
    return f"Lista {lid}"


def _resolver_nombres_lista(nombres_lista_precio: dict[int, str] | None) -> dict[int, str]:
    if nombres_lista_precio is not None:
        return nombres_lista_precio
    bu = os.getenv("DRR_API_BASE_URL", "").strip()
    if not bu:
        return {}
    try:
        from drr.lista_precios import nombres_listas_precio

        key = os.getenv("DRR_API_KEY", "").strip() or None
        return nombres_listas_precio(bu, key)
    except Exception:
        return {}


def linea_producto_resumen(
    p: Producto,
    *,
    include_prices: bool = True,
    nombres_lista_precio: dict[int, str] | None = None,
    solo_lista_precio_id: int | None = None,
) -> str:
    """
    Bloque para listados DRR: cabecera + precioFinal por presentación (Bulto/Unidad) y por lista de precio.
    Usa la descripción del catálogo /Empresa/ListaPrecio (sin etiquetas L0/L1).
    """
    head = f"• {p.descripcion}"
    if (p.codigo_barras or "").strip():
        head += f" | Cód: {p.codigo_barras}"
    if not include_prices:
        return head

    nombres = _resolver_nombres_lista(nombres_lista_precio)
    bloques = p.precios_por_presentacion_final(solo_lista_id=solo_lista_precio_id)
    lines = [head]
    if bloques:
        for label_pres, tuples in bloques:
            partes = [
                f"{_descripcion_lista_precio(lid, nombres)}: ${v:.2f}" for lid, v in tuples
            ]
            lines.append(f"  {label_pres}: " + " · ".join(partes))
    else:
        pares = p.precios_finales_por_lista(solo_lista_id=solo_lista_precio_id)
        if pares:
            partes = [f"{_descripcion_lista_precio(lid, nombres)}: ${v:.2f}" for lid, v in pares]
            lines.append("  " + " · ".join(partes))
        elif solo_lista_precio_id is not None:
            nm = _descripcion_lista_precio(solo_lista_precio_id, nombres)
            lines.append(f"  {nm}: (sin precioFinal en API)")
        elif p.precio is not None:
            lines.append(f"  ${p.precio:.2f}")
        else:
            lines.append("  Precio: (no informado en API)")
    return "\n".join(lines)


def formato_lista(productos: list[Producto], max_items: int = 10) -> str:
    """
    Genera texto para listado en Telegram: número, indicador de imagen, código (si existe) y descripción.
    No muestra corchetes vacíos cuando no hay código de barras; mejora legibilidad.
    """
    if not productos:
        return "No se encontraron productos."
    lines = []
    for i, p in enumerate(productos[:max_items], 1):
        # Indicador: imagen asignada vs sin imagen (evita "[]" confuso en cliente)
        img = "🖼" if p.imagen_url else "⬜"
        codigo = (p.codigo_barras or "").strip()
        desc = (p.descripcion or "").strip()
        desc_short = desc[:50] + "…" if len(desc) > 50 else desc
        # Una línea por producto; código solo si existe (sin corchetes vacíos)
        if codigo:
            line = f"{i}. {img} {desc_short} · Cód: {codigo}"
        else:
            line = f"{i}. {img} {desc_short}"
        lines.append(line)
    return "\n".join(lines)


def formato_detalle(
    p: Producto,
    *,
    nombres_lista_precio: dict[int, str] | None = None,
    solo_lista_precio_id: int | None = None,
) -> str:
    """Texto para detalle de un producto (info básica y clara)."""
    parts = [
        f"🆔 ID: {p.id}",
        f"📋 Código: {p.codigo_barras}",
        f"📝 Descripción: {p.descripcion}",
    ]
    nombres = _resolver_nombres_lista(nombres_lista_precio)
    bloques = p.precios_por_presentacion_final(solo_lista_id=solo_lista_precio_id)
    if bloques:
        parts.append("💰 Precios finales (precioFinal por lista):")
        for label_pres, tuples in bloques:
            parts.append(f"   {label_pres}:")
            for lid, val in tuples:
                parts.append(f"      • {_descripcion_lista_precio(lid, nombres)}: ${val:.2f}")
    else:
        pares = p.precios_finales_por_lista(solo_lista_id=solo_lista_precio_id)
        if pares:
            parts.append("💰 Precios finales:")
            for lid, val in pares:
                parts.append(f"   • {_descripcion_lista_precio(lid, nombres)}: ${val:.2f}")
        elif solo_lista_precio_id is not None:
            nm = _descripcion_lista_precio(solo_lista_precio_id, nombres)
            parts.append(f"💰 {nm}: (sin precioFinal en API)")
        elif p.precio is not None:
            parts.append(f"💰 Precio: {p.precio}")
    if p.stock is not None:
        parts.append(f"📦 Stock: {p.stock}")
    if p.imagen_url:
        parts.append(f"🖼 Imagen: {_imagen_para_texto(p.imagen_url)}")
    return "\n".join(parts)
