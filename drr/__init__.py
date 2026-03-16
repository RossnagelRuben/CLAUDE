# Módulo DRR: integración API de productos con búsqueda de imágenes (DuckDuckGo).
# Ver docs/DOCUMENTACION_MIGRACION_DRR.md para contrato API y comandos Telegram.

from drr.models import Producto
from drr.service import ServicioProductos

__all__ = ["Producto", "ServicioProductos"]
