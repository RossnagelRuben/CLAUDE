#!/usr/bin/env python3
"""Arranca API interna (docker0) + servidor de imágenes públicas en un solo servicio systemd."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

ROOT = "/root/telegram-bot"
PY = os.path.join(ROOT, ".venv-bridge", "bin", "python")


def main() -> None:
    os.chdir(ROOT)
    os.makedirs(os.path.join(ROOT, "data", "sd-bridge-images"), exist_ok=True)
    port = os.environ.get("SD_BRIDGE_PUBLIC_PORT", "17860")

    procs: list[subprocess.Popen] = []

    def terminate_children() -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 8
        for p in procs:
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if p.poll() is None:
                p.kill()

    def on_signal(*_: object) -> None:
        terminate_children()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    procs.append(
        subprocess.Popen(
            [
                PY,
                "-m",
                "uvicorn",
                "sd_a1111_bridge:public_app",
                "--host",
                "0.0.0.0",
                "--port",
                port,
            ],
            cwd=ROOT,
        )
    )
    procs.append(
        subprocess.Popen(
            [
                PY,
                "-m",
                "uvicorn",
                "sd_a1111_bridge:app",
                "--host",
                "172.17.0.1",
                "--port",
                "7860",
            ],
            cwd=ROOT,
        )
    )

    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    terminate_children()
                    sys.exit(p.returncode if p.returncode else 1)
            time.sleep(0.3)
    except KeyboardInterrupt:
        on_signal()


if __name__ == "__main__":
    main()
