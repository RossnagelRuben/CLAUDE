# Formateo de productos para Telegram. Una sola responsabilidad: texto legible y breve.
# Principio SRP (SOLID): este módulo solo se encarga de dar formato a datos de producto.

from drr.models import Producto


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


def formato_detalle(p: Producto) -> str:
    """Texto para detalle de un producto (info básica y clara)."""
    parts = [
        f"🆔 ID: {p.id}",
        f"📋 Código: {p.codigo_barras}",
        f"📝 Descripción: {p.descripcion}",
    ]
    if p.precio is not None:
        parts.append(f"💰 Precio: {p.precio}")
    if p.stock is not None:
        parts.append(f"📦 Stock: {p.stock}")
    if p.imagen_url:
        parts.append(f"🖼 Imagen: {p.imagen_url[:80]}…" if len(p.imagen_url) > 80 else f"🖼 Imagen: {p.imagen_url}")
    return "\n".join(parts)
