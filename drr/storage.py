# Almacén local de imágenes por producto. Implementa IAlmacenImagenes.
# Guarda en carpeta productos_imagenes/{id_producto}.jpg (o nombre sugerido).

import logging
from pathlib import Path

from drr.interfaces import IAlmacenImagenes

logger = logging.getLogger(__name__)


class AlmacenImagenesLocal(IAlmacenImagenes):
    """Guarda imágenes en disco por ID de producto; devuelve ruta local."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, id_producto: int | str) -> Path:
        return self.base_dir / f"{id_producto}.jpg"

    def guardar(self, id_producto: int | str, imagen_bytes: bytes, nombre_sugerido: str = "imagen.jpg") -> str:
        path = self.base_dir / f"{id_producto}.jpg"
        path.write_bytes(imagen_bytes)
        logger.info("Imagen guardada: %s", path)
        return str(path)

    def obtener_ruta(self, id_producto: int | str) -> str | None:
        path = self._path(id_producto)
        if path.exists():
            return str(path)
        return None
