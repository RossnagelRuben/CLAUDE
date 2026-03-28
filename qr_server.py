"""
Servidor web opcional para ver el QR de Evolution API (mismo HTML que ``/evolution/`` en el bridge).

La lógica vive en ``evolution_qr.py``. Si ya usás ``whatsapp_bridge`` en el puerto 8766,
podés abrir **http://TU_IP:8766/evolution/** y no hace falta levantar este proceso.

Variable opcional: QR_SERVER_PORT (default 8099), bind 0.0.0.0

Uso:
    ./venv/bin/python qr_server.py
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from evolution_qr import build_api_qr_response, handle_logout_post_body, html_qr_page

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PORT = int(os.getenv("QR_SERVER_PORT", "8099"))
# Misma API relativa que antes: GET /api/qr, POST /api/logout
_QR_API_PREFIX = "/api"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/qr":
            self._send_json(build_api_qr_response())
            return
        if path in ("/", ""):
            self._send_html(html_qr_page(api_prefix=_QR_API_PREFIX))
            return
        if path == "/qr_live.html":
            live = BASE_DIR / "qr_live.html"
            if live.is_file():
                self._send_html(live.read_text(encoding="utf-8", errors="replace"))
                return
            self.send_error(404)
            return
        if path == "/jarvis_qr.png":
            png = BASE_DIR / "jarvis_qr.png"
            if png.is_file():
                data = png.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/logout":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        code, payload = handle_logout_post_body(raw, dict(self.headers.items()))
        self._send_json(payload, code)


def main() -> None:
    httpd = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"QR web (opcional): http://0.0.0.0:{PORT}/  — mismo contenido que el bridge en /evolution/")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
