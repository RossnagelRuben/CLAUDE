"""Servidor web simple para mostrar el QR de WhatsApp."""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import json

EVOLUTION_URL = "http://localhost:8080"
EVOLUTION_KEY = "jarvis-secret"
INSTANCE = "jarvis"
PORT = 8888

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jarvis — Vincular WhatsApp</title>
<style>
  body {{ background: #111; color: #fff; font-family: sans-serif;
          display: flex; flex-direction: column; align-items: center;
          justify-content: center; min-height: 100vh; margin: 0; }}
  h1 {{ color: #25d366; margin-bottom: 8px; }}
  p {{ color: #aaa; margin-bottom: 24px; text-align: center; }}
  img {{ border-radius: 12px; width: 280px; height: 280px; background: #fff; padding: 12px; }}
  .error {{ color: #ff6b6b; font-size: 14px; }}
  .reload {{ margin-top: 20px; background: #25d366; color: #fff; border: none;
             padding: 12px 28px; border-radius: 8px; font-size: 16px;
             cursor: pointer; text-decoration: none; }}
  .reload:hover {{ background: #1ebe5d; }}
  .steps {{ text-align: left; background: #222; padding: 20px 28px;
            border-radius: 10px; margin-top: 24px; max-width: 320px; }}
  .steps li {{ margin-bottom: 8px; color: #ccc; }}
</style>
</head>
<body>
<h1>📱 Jarvis — WhatsApp</h1>
<p>Escaneá el QR con WhatsApp para vincular Jarvis</p>
{content}
<ol class="steps">
  <li>Abrí WhatsApp en tu celular</li>
  <li>Menú (⋮) → <b>Dispositivos vinculados</b></li>
  <li>Tocá <b>Vincular un dispositivo</b></li>
  <li>Apuntá la cámara al QR de arriba</li>
</ol>
<a href="/" class="reload">🔄 Actualizar QR</a>
</body>
</html>"""


def get_qr():
    req = urllib.request.Request(
        f"{EVOLUTION_URL}/instance/connect/{INSTANCE}",
        headers={"apikey": EVOLUTION_KEY},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    return data.get("base64", ""), data.get("pairingCode")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            b64, code = get_qr()
            if b64:
                content = f'<img src="{b64}" alt="QR WhatsApp">'
                if code:
                    content += f'<p style="color:#25d366;font-size:22px;letter-spacing:4px"><b>{code}</b></p>'
            else:
                content = '<p class="error">✅ Ya vinculado o QR no disponible.<br>Si recién escaneaste, ¡listo!</p>'
        except Exception as e:
            content = f'<p class="error">Error obteniendo QR: {e}</p>'
        self.wfile.write(HTML.format(content=content).encode())


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"QR server corriendo en http://104.225.140.9:{PORT}")
    server.serve_forever()
