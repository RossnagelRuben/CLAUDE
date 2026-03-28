# Cliente HTTP para la API DRR. Implementa IProductoRepository.
# Responsabilidad única: traducir llamadas a HTTP y mapear respuestas a Producto.
#
# Incluye:
# - Caché local opcional (TTL) para evitar llamadas repetidas.
# - TokenProvider opcional (cliente→dev→usuario→final) sin hardcodear credenciales.

import base64
import json
import logging
import os
import urllib.error
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


def _productos_desde_getproducto_payload(data_get: dict | list | None) -> list[Producto]:
    if not isinstance(data_get, dict):
        return []
    if isinstance(data_get.get("data"), list):
        return [Producto.from_api_dict(item) for item in data_get["data"]]
    if isinstance(data_get.get("Data"), list):
        return [Producto.from_api_dict(item) for item in data_get["Data"]]
    return []


def _parse_lista_precio_catalog(data: dict | list | None) -> dict[int, str]:
    """Normaliza respuesta de /Empresa/ListaPrecio a {listaPrecID: descripción}."""
    out: dict[int, str] = {}
    rows: list = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("data", "Data", "items", "Items", "result", "Result"):
            v = data.get(key)
            if isinstance(v, list):
                rows = v
                break
    for row in rows:
        if not isinstance(row, dict):
            continue
        lid = None
        for k in ("listaPrecID", "ListaPrecID", "listaPrecId"):
            if k in row and row[k] is not None:
                lid = row[k]
                break
        try:
            lid_i = int(lid)
        except (TypeError, ValueError):
            continue
        desc = row.get("descripcion") or row.get("Descripcion") or row.get("nombre") or row.get("Nombre")
        label = str(desc).strip() if desc is not None else ""
        out[lid_i] = label if label else f"Lista {lid_i}"
    return out


def fetch_product_image_bytes_for_snapshot(
    snapshot: dict,
    *,
    base_url: str,
    api_key: str | None = None,
    timeout: int = 20,
) -> bytes | None:
    """
    Obtiene bytes de imagen para un ítem del último listado (to_snapshot).
    El listado suele pedirse con Include=1 (precios); la imagen en DRR requiere Include=2 (Observaciones), ver Swagger.
    """
    from drr.models import load_drr_product_image_bytes

    base = (base_url or "").strip()
    if not base:
        return None

    url0 = (snapshot.get("imagen_url") or "").strip()
    if url0:
        b = load_drr_product_image_bytes(url0)
        if b:
            return b

    img_include = os.getenv("DRR_IMAGEN_INCLUDE", "2").strip() or "2"
    client = DRRProductoAPIClient(
        base.rstrip("/"),
        api_key=api_key,
        timeout=timeout,
        cache_ttl_seconds=25,
    )
    try:
        p = client.obtener_con_include_para_imagen(
            codigo_id=snapshot.get("id"),
            codigo_barra=snapshot.get("codigo_barras"),
            include=img_include,
        )
    except Exception as e:
        logger.warning("DRR refetch imagen (Include=%s): %s", img_include, e)
        return None
    if not p or not (p.imagen_url or "").strip():
        return None
    ref = p.imagen_url.strip()
    b = load_drr_product_image_bytes(ref)
    if not b:
        logger.warning(
            "DRR imagen: no se pudieron decodificar/descargar bytes (ref %s chars)",
            len(ref),
        )
    return b


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

    def catalogo_listas_precio(self) -> dict[int, str]:
        """
        GET /Empresa/ListaPrecio — nombres por listaPrecID (Swagger DRR).
        """
        try:
            data = self._request(
                "/Empresa/ListaPrecio",
                {"Inhabilitado": "false", "PageNumber": 1, "PageSize": 500},
            )
        except Exception as e:
            logger.warning("DRR ListaPrecio: %s", e)
            return {}
        return _parse_lista_precio_catalog(data)

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
        # Include (según Blazor/ProductosAPI): 0=ninguno, 1=Stock (presentaciones + listaPrecio), 2=Observaciones.
        # Antes forzábamos 2 (pantalla “asignar imágenes”); eso suele omitir precios anidados → el bot mostraba
        # “no informado”. Por defecto 1 para precios. Override: DRR_GET_PRODUCTO_INCLUDE=2|3|… o vacío para no enviar.
        fallback_params: dict = {
            "pageSize": min(page_size, 100),
            "pageNumber": 1,
            "Imagen": "true",
        }
        include_raw = os.getenv("DRR_GET_PRODUCTO_INCLUDE", "1").strip()
        if include_raw:
            fallback_params["Include"] = include_raw
        if descripcion:
            fallback_params["descripcionLarga"] = descripcion
        if codigo_barras:
            fallback_params["codigoBarra"] = codigo_barras
        if con_codigo_barra is not None:
            fallback_params["ConCodigoBarra"] = "true" if con_codigo_barra else "false"
        data_get = self._request("/Producto/GetProducto", fallback_params)
        out = _productos_desde_getproducto_payload(data_get)
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

    def obtener_con_include_para_imagen(
        self,
        *,
        codigo_id: int | str | None = None,
        codigo_barra: str | None = None,
        include: str = "2",
    ) -> Producto | None:
        """
        Re-consulta un producto con Include=2 (Observaciones): en DRR suele exponerse ahí la imagen asignada.
        Ver GET /Producto en Swagger (Include: 0/1/2).
        """
        cb = (codigo_barra or "").strip()
        cid = codigo_id
        params_gp: dict = {
            "pageNumber": 1,
            "pageSize": min(50, 100),
            "Imagen": "true",
            "Include": include,
        }
        if cb:
            params_gp["codigoBarra"] = cb
        elif cid is not None and str(cid).strip() != "":
            params_gp["codigoID"] = cid
        else:
            return None

        try:
            data_get = self._request("/Producto/GetProducto", params_gp)
        except Exception as e:
            logger.warning("DRR GetProducto (imagen) falló: %s", e)
            data_get = None

        out = _productos_desde_getproducto_payload(data_get)
        if cb and len(out) > 1:
            for p in out:
                if (p.codigo_barras or "").strip() == cb:
                    return p
        if out:
            return out[0]

        if cid is not None and str(cid).strip() != "":
            try:
                data = self._request(
                    "/Producto",
                    {
                        "CodigoID": cid,
                        "PageNumber": 1,
                        "PageSize": 1,
                        "Imagen": "true",
                        "Include": include,
                    },
                )
            except Exception as e:
                logger.warning("DRR GET /Producto CodigoID (imagen): %s", e)
                return None
            alt = _parse_list_response(data)
            if alt:
                return alt[0]
        return None

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

    def patch_producto(self, payload: dict) -> tuple[bool, str]:
        """
        PATCH /Producto (Swagger: ProductoPatchRequest).
        Típico: {\"codigoID\": n, \"imagen\": \"<base64>\"} — imagen como string base64 (ByteArrayPatch).
        """
        url = f"{self.base_url}/Producto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="PATCH",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
            req.add_header("X-API-Key", self.api_key)
        elif self.token_provider and hasattr(self.token_provider, "auth_header"):
            try:
                req.add_header("Authorization", self.token_provider.auth_header())
            except Exception as e:
                logger.warning("No se pudo obtener token DRR (patch): %s", e)
        try:
            logger.info("DRR PATCH %s bytes_body=%s", url, len(body))
            with urllib.request.urlopen(req, timeout=max(self.timeout, 60)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                logger.info("DRR PATCH OK %s resp_len=%s", url, len(raw or ""))
                return True, (raw[:500] if raw else "OK")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:800]
            logger.warning("DRR PATCH HTTP %s: %s", e.code, err_body)
            return False, f"HTTP {e.code}: {err_body}"
        except Exception as e:
            logger.exception("DRR PATCH falló: %s", e)
            return False, str(e)

    @staticmethod
    def imagen_bytes_para_patch(imagen_bytes: bytes) -> str:
        """Base64 estándar (sin prefijo data:) para el campo imagen del ProductoPatchRequest."""
        return base64.standard_b64encode(imagen_bytes).decode("ascii")
