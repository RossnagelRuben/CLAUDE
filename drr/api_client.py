# Cliente HTTP para la API DRR. Implementa IProductoRepository.
# Responsabilidad única: traducir llamadas a HTTP y mapear respuestas a Producto.
#
# Incluye:
# - Caché local opcional (TTL) para evitar llamadas repetidas.
# - TokenProvider opcional (cliente→dev→usuario→final) sin hardcodear credenciales.

import json
import logging
import urllib.request
from urllib.parse import urlencode

from drr.models import Producto
from drr.interfaces import IProductoRepository
from drr.cache import TTLCache

logger = logging.getLogger(__name__)


def _parse_list_response(data: dict | list) -> list[Producto]:
    """Convierte respuesta de listado (lista o dict con items/data) en list[Producto]."""
    if isinstance(data, list):
        return [Producto.from_api_dict(item) for item in data]
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        return [Producto.from_api_dict(item) for item in data["items"]]
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        return [Producto.from_api_dict(item) for item in data["data"]]
    if isinstance(data, dict) and "Data" in data and isinstance(data["Data"], list):
        return [Producto.from_api_dict(item) for item in data["Data"]]
    return []


class DRRProductoAPIClient(IProductoRepository):
    """
    Cliente que consume la API REST de productos DRR.
    Base URL y opcional API key se inyectan (desde .env en el bot).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 15,
        *,
        token_provider: object | None = None,
        cache_ttl_seconds: int = 25,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.token_provider = token_provider
        self.cache = TTLCache[str, dict | list](ttl_seconds=cache_ttl_seconds, max_items=300)
        # Última petición (para mostrar log cuando no hay resultados)
        self._last_request_info: dict = {}

    def last_request_info(self) -> dict:
        """Devuelve datos de la última petición: url, response_bytes, from_cache."""
        return dict(self._last_request_info)

    def _request(self, path: str, params: dict | None = None) -> dict | list:
        """GET request; devuelve JSON como dict o list. Lanza en errores HTTP.

        También registra en logs de servidor:
        - URL completa (con query)
        - Si vino de caché o de la API
        - Tamaño de respuesta (len de texto)
        """
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        cache_key = url
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.info("DRR GET (cache) %s", url)
            self._last_request_info = {"url": url, "response_bytes": 0, "from_cache": True}
            return cached
        logger.info("DRR GET %s", url)
        req = urllib.request.Request(url)
        # Prioridad:
        # 1) api_key explícita (DRR_API_KEY)
        # 2) token_provider (flujo cliente→dev→usuario→final)
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
            req.add_header("X-API-Key", self.api_key)
        elif self.token_provider and hasattr(self.token_provider, "auth_header"):
            try:
                req.add_header("Authorization", self.token_provider.auth_header())
            except Exception as e:
                logger.warning("No se pudo obtener token DRR (token_provider): %s", e)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                logger.info("DRR resp %s bytes=%s", url, len(raw or ""))
                parsed = json.loads(raw) if raw else []
                self.cache.set(cache_key, parsed)
                self._last_request_info = {"url": url, "response_bytes": len(raw or ""), "from_cache": False}
                return parsed
        except urllib.error.HTTPError as e:
            logger.warning("DRR API HTTP error %s: %s", e.code, e.read().decode("utf-8")[:200])
            raise
        except Exception as e:
            logger.exception("DRR API request failed: %s", e)
            raise

    def listar(
        self,
        descripcion: str | None = None,
        codigo_barras: str | None = None,
        limit: int = 10,
        con_imagen: bool | None = None,
        con_codigo_barra: bool | None = None,
    ) -> list[Producto]:
        # Usar GetProducto (como la app Blazor) para poder usar ConCodigoBarra y filtrar por imagen en cliente
        page_size = max(limit, 50) if con_imagen is not None else limit
        fallback_params: dict = {
            "pageSize": min(page_size, 100),
            "pageNumber": 1,
            "Imagen": "true",
            "Include": "2",
        }
        if descripcion:
            fallback_params["descripcionLarga"] = descripcion
        if codigo_barras:
            fallback_params["codigoBarra"] = codigo_barras
        if con_codigo_barra is not None:
            fallback_params["ConCodigoBarra"] = "true" if con_codigo_barra else "false"
        data_get = self._request("/Producto/GetProducto", fallback_params)
        out = []
        if isinstance(data_get, dict) and "data" in data_get and isinstance(data_get["data"], list):
            out = [Producto.from_api_dict(item) for item in data_get["data"]]
        elif isinstance(data_get, dict) and "Data" in data_get and isinstance(data_get["Data"], list):
            out = [Producto.from_api_dict(item) for item in data_get["Data"]]
        if not out:
            # Fallback a GET /Producto por si GetProducto no existe en esta API
            params = {}
            if descripcion:
                params["Search"] = descripcion
            if codigo_barras:
                params["CodigoBarra"] = codigo_barras
            data = self._request("/Producto", params)
            out = _parse_list_response(data)
        # Filtro por imagen en cliente (la API puede no soportarlo)
        if con_imagen is not None:
            if con_imagen:
                out = [p for p in out if p.imagen_url]
            else:
                out = [p for p in out if not p.imagen_url]
        return out[:limit]

    def obtener_por_id(self, id_producto: int | str) -> Producto | None:
        try:
            # Swagger: GET /Producto?CodigoID=...
            data = self._request("/Producto", {"CodigoID": id_producto})
            if isinstance(data, list) and data:
                return Producto.from_api_dict(data[0])
            if isinstance(data, dict) and "items" in data and data["items"]:
                return Producto.from_api_dict(data["items"][0])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return None

    def obtener_por_codigo(self, codigo: str) -> Producto | None:
        lista = self.listar(codigo_barras=codigo, limit=1)
        return lista[0] if lista else None
