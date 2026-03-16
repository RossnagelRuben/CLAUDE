"""
Flujo de tokens DRR (cliente → dev → usuario → final) con caché.

IMPORTANTE (seguridad):
- NO hardcodear credenciales en el código.
- Configurarlas por variables de entorno en el servidor (.env).

Este módulo está alineado al Swagger público de DRR (`/swagger/v1/swagger.json`):
- `POST /Auth/TokenDeveloper` (Bearer = Token DRR empresa/cliente)
- `POST /Auth/TokenUser` (Bearer = Token DEV; body incluye user/pwd)

Según Swagger, muchos endpoints requieren:
- `Authorization: Bearer TokenDev.TokenUser`
Es decir: el token “final” se arma concatenando ambos con un punto.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TokenConfig:
    base_url: str
    # Token empresa/cliente (primer eslabón)
    token_cliente: str
    # Usuario/contraseña (para token usuario)
    usuario: str
    password: str

    # Endpoints (paths) configurables. Ajustar a DRR real.
    path_token_dev: str = "/Auth/TokenDeveloper"
    path_token_usuario: str = "/Auth/TokenUser"

    timeout: int = 15


class DRRTokenProvider:
    """
    Proveedor de token final para DRR APIs.

    Regla pedida:
    - Por defecto usar token cliente para obtener token dev,
      luego token usuario, luego token final.
    """

    def __init__(self, cfg: TokenConfig, ttl_seconds: int = 55 * 60):
        self.cfg = cfg
        self.ttl_seconds = int(ttl_seconds)
        self._final_token: str | None = None
        self._expires_at: float = 0.0

    def get_final_token(self) -> str:
        now = time.time()
        if self._final_token and now < self._expires_at:
            return self._final_token

        dev_token = self._obtener_token_dev()      # str (token DEV)
        user_token = self._obtener_token_usuario(dev_token)  # str (token USER)
        # Swagger: Bearer = TokenDev.TokenUser
        final_token = f"{dev_token}.{user_token}"

        self._final_token = final_token
        self._expires_at = now + self.ttl_seconds
        return final_token

    def auth_header(self) -> str:
        return f"Bearer {self.get_final_token()}"

    def _post_json(self, path: str, payload: dict, auth_bearer: str | None) -> dict | str:
        url = f"{self.cfg.base_url.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if auth_bearer:
            req.add_header("Authorization", auth_bearer)
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            raw = resp.read().decode("utf-8")
            # Puede venir un string JSON, un objeto JSON o vacío
            return json.loads(raw) if raw else {}

    @staticmethod
    def _extraer_token(data: dict | str) -> str:
        # Permite diferentes convenciones típicas o un string plano.
        if isinstance(data, str):
            return data.strip()
        return (
            data.get("token")
            or data.get("access_token")
            or data.get("accessToken")
            or data.get("jwt")
            or ""
        )

    def _obtener_token_dev(self) -> str:
        # Swagger:
        # - Endpoint: POST /Auth/TokenDeveloper
        # - Bearer: TOKEN CLIENTE (DRR_CLIENT_TOKEN en .env)
        # - Body: TokenRequest { user, pwd, usePublicLogin }
        payload = {"user": None, "pwd": None, "usePublicLogin": False}
        data = self._post_json(self.cfg.path_token_dev, payload, auth_bearer=f"Bearer {self.cfg.token_cliente}")
        token = self._extraer_token(data)
        if not token:
            raise RuntimeError("DRR: no se pudo obtener token dev (respuesta sin token). Ajustá paths/contrato.")
        return token

    def _obtener_token_usuario(self, token_dev: str) -> str:
        # Swagger:
        # - Endpoint: POST /Auth/TokenUser
        # - Bearer: TOKEN DEV devuelto por /Auth/TokenDeveloper
        # - Body: TokenRequest con el usuario real de DRR
        payload = {"user": self.cfg.usuario, "pwd": self.cfg.password, "usePublicLogin": False}
        data = self._post_json(self.cfg.path_token_usuario, payload, auth_bearer=f"Bearer {token_dev}")
        token = self._extraer_token(data)
        if not token:
            raise RuntimeError("DRR: no se pudo obtener token usuario (respuesta sin token). Ajustá paths/contrato.")
        return token

