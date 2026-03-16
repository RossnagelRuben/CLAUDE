# Interfaces (protocolos) para inversión de dependencias (SOLID).
# El bot depende de estas abstracciones, no de implementaciones concretas.

from typing import Protocol, runtime_checkable

from drr.models import Producto


@runtime_checkable
class IProductoRepository(Protocol):
    """Contrato para obtener productos desde la API DRR (o mock)."""

    def listar(
        self,
        descripcion: str | None = None,
        codigo_barras: str | None = None,
        limit: int = 10,
        con_imagen: bool | None = None,
        con_codigo_barra: bool | None = None,
    ) -> list[Producto]:
        """Lista productos con filtros opcionales. con_imagen/con_codigo_barra True=con, False=sin."""
        ...

    def obtener_por_id(self, id_producto: int | str) -> Producto | None:
        """Obtiene un producto por ID."""
        ...

    def obtener_por_codigo(self, codigo: str) -> Producto | None:
        """Obtiene un producto por código de barras."""
        ...


@runtime_checkable
class IBuscadorImagenes(Protocol):
    """Contrato para buscar imágenes (p. ej. DuckDuckGo)."""

    def buscar(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Devuelve lista de dicts con al menos: 'image' o 'url' (URL de imagen), 'title' opcional.
        """
        ...


@runtime_checkable
class IAlmacenImagenes(Protocol):
    """Contrato para guardar/recuperar imágenes por producto (local o API)."""

    def guardar(self, id_producto: int | str, imagen_bytes: bytes, nombre_sugerido: str = "imagen.jpg") -> str:
        """Guarda la imagen y devuelve ruta o URL."""
        ...

    def obtener_ruta(self, id_producto: int | str) -> str | None:
        """Devuelve la ruta o URL de la imagen del producto si existe."""
        ...
