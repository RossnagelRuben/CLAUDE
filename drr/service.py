# Servicio de aplicación: orquesta repositorio, búsqueda de imágenes y almacén.
# No conoce Telegram; solo lógica de negocio y formato de texto.

import logging
import urllib.request
from typing import Any

from drr.models import Producto
from drr.interfaces import IProductoRepository, IBuscadorImagenes, IAlmacenImagenes
from drr.formatter import formato_lista, formato_detalle

logger = logging.getLogger(__name__)


class ServicioProductos:
    """
    Casos de uso: listar, ver, buscar imagen, guardar imagen, ver imagen actual.
    Depende de abstracciones (SOLID); las implementaciones se inyectan.
    """

    def __init__(
        self,
        repo: IProductoRepository,
        buscador_imagenes: IBuscadorImagenes,
        almacen: IAlmacenImagenes,
    ):
        self.repo = repo
        self.buscador_imagenes = buscador_imagenes
        self.almacen = almacen

    def listar(
        self,
        descripcion: str | None = None,
        codigo_barras: str | None = None,
        limit: int = 10,
        con_imagen: bool | None = None,
        con_codigo_barra: bool | None = None,
    ) -> tuple[str, list[Producto]]:
        """Lista productos con filtros; devuelve texto formateado y lista."""
        productos = self.repo.listar(
            descripcion=descripcion,
            codigo_barras=codigo_barras,
            limit=limit,
            con_imagen=con_imagen,
            con_codigo_barra=con_codigo_barra,
        )
        return formato_lista(productos, max_items=limit), productos

    def ver(self, id_o_codigo: str) -> tuple[str, Producto | None]:
        """Obtiene detalle de un producto por ID o código de barras."""
        id_o_codigo = id_o_codigo.strip()
        p = None
        if id_o_codigo.isdigit():
            p = self.repo.obtener_por_id(int(id_o_codigo))
        if p is None:
            p = self.repo.obtener_por_codigo(id_o_codigo)
        if p is None:
            return "Producto no encontrado.", None
        return formato_detalle(p), p

    def buscar_imagen(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Busca imágenes por texto (código o descripción). Devuelve lista con 'url', 'image', 'title'."""
        return self.buscador_imagenes.buscar(query.strip(), max_results=max_results)

    def guardar_imagen_desde_url(self, id_producto: int | str, imagen_url: str) -> str | None:
        """Descarga la imagen desde URL y la guarda en el almacén para el producto."""
        try:
            req = urllib.request.Request(imagen_url, headers={"User-Agent": "JarvisBot/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                imagen_bytes = resp.read()
            return self.almacen.guardar(id_producto, imagen_bytes)
        except Exception as e:
            logger.warning("Error descargando/guardando imagen: %s", e)
            return None

    def ver_imagen_actual(self, id_producto: int | str) -> tuple[bytes | None, str | None]:
        """
        Devuelve (bytes de la imagen, ruta) si existe en almacén local.
        No descarga desde API; solo almacén local.
        """
        ruta = self.almacen.obtener_ruta(id_producto)
        if not ruta:
            return None, None
        try:
            with open(ruta, "rb") as f:
                return f.read(), ruta
        except OSError:
            return None, None

    @staticmethod
    def prompt_para_mejorar_imagen(producto: Producto, descripcion_extra: str = "") -> str:
        """Sugiere un prompt para generar/mejorar imagen del producto con IA."""
        base = f"Producto de supermercado o tienda: {producto.descripcion}. Envase o producto aislado, fondo blanco o neutro, buena iluminación."
        if descripcion_extra:
            base += f" {descripcion_extra}"
        return base
