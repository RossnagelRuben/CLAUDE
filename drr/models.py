# Modelo de dominio para producto (DRR). Una sola responsabilidad: representar datos.

from dataclasses import dataclass
from typing import Any


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
        codigo_barras = str(_get("codigoBarras", "codigo_barras", "codigoBarra") or "")
        descripcion = str(_get("descripcion", "descripcionLarga", "DescripcionLarga", "descripcionCorta") or "").strip()
        imagen_url = _get("imagenUrl", "imagen_url", "imagen") or None

        return cls(
            id=id_val,
            codigo_barras=codigo_barras,
            descripcion=descripcion,
            imagen_url=imagen_url,
            precio=_get("precio", "precio"),
            stock=_get("stock", "stock"),
            extra={k: v for k, v in data.items() if k not in (
                "id", "codigoID", "CodigoID", "codigoBarras", "codigo_barras", "codigoBarra",
                "descripcion", "descripcionLarga", "DescripcionLarga", "descripcionCorta",
                "imagenUrl", "imagen_url", "imagen", "precio", "stock"
            )},
        )
