#!/usr/bin/env python3
"""
Escribe /root/telegram-bot/jarvis_qr.png periódicamente desde Evolution (para qr_live.html + http.server).

Instancia y API se leen de `.env` (EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE).
"""
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_BASE = (os.getenv("EVOLUTION_API_URL") or "http://localhost:8080").strip().rstrip("/")
API_KEY = (os.getenv("EVOLUTION_API_KEY") or "").strip()
INSTANCE = (os.getenv("EVOLUTION_INSTANCE") or "jarvis").strip()
OUT = BASE_DIR / "jarvis_qr.png"


def fetch_and_write_qr() -> bool:
    if not API_KEY:
        return False
    req = urllib.request.Request(
        f"{API_BASE}/instance/connect/{INSTANCE}",
        headers={"apikey": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    b64 = data.get("base64") or ""
    if not b64.startswith("data:image/png;base64,"):
        return False
    raw = base64.b64decode(b64.split(",", 1)[1])
    OUT.write_bytes(raw)
    return True


def main() -> None:
    while True:
        try:
            fetch_and_write_qr()
        except Exception:
            pass
        time.sleep(2)


if __name__ == "__main__":
    main()
