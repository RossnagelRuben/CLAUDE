"""
Mini visor web para Miniverse.

Objetivo: ofrecer una forma muy simple de lanzar algunos escenarios de Miniverse
desde el navegador y ver la salida de la simulación en texto, sin modificar el
núcleo de la librería.

Diseño:
- Servidor FastAPI muy ligero.
- Frontend HTML mínimo (un formulario con selector de escenario y ticks).
- Cuando el usuario pulsa "Run", el servidor ejecuta la CLI `miniverse run ...`
  en un subproceso y devuelve la salida estándar como texto plano.

Notas importantes:
- Esto es una demo: ejecuta simulaciones bajo demanda y devuelve el log; no es
  un panel en tiempo real ni un dashboard persistente.
- Asume que este proceso se ejecuta dentro del mismo entorno virtual donde está
  instalado `miniverse` y que el ejecutable `miniverse` está en PATH.
"""

from __future__ import annotations

import html
import subprocess
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, PlainTextResponse

BASE_DIR = Path(__file__).resolve().parent

# Escenarios "conocidos" para exponer en la UI.
# Se pueden añadir más sin tocar el resto del código.
SCENARIOS: dict[str, str] = {
    "demo/workshop": "Workshop demo (determinista, 3 agentes)",
    "demo/valentines": "Valentines demo (Smallville style)",
    "examples/workshop": "Workshop example (código de ejemplos)",
}

DEFAULT_TICKS = 10

app = FastAPI(
    title="Miniverse Web Demo",
    description="Visor mínimo para lanzar escenarios de Miniverse desde el navegador.",
    version="0.1.0",
)


def _run_miniverse_cli(scenario: str, ticks: int) -> tuple[int, str]:
    """
    Ejecuta `miniverse run <scenario>` como subproceso y captura la salida.

    Se usa la CLI oficial para no acoplarse a APIs internas de Miniverse, lo que
    hace que esta capa web sea fácil de mantener aunque el core evolucione.
    """
    scenario = scenario.strip()
    if not scenario:
        return 1, "Escenario vacío. Elegí un escenario válido."

    # Construimos el comando. `ticks` es opcional según el escenario, pero ayuda
    # a que las demos terminen en un tiempo razonable.
    cmd = ["miniverse", "run", scenario]
    if ticks > 0:
        cmd.extend(["--ticks", str(ticks)])

    try:
        # Usamos bash -lc para respetar el PATH del entorno virtual si este
        # servidor se lanza con .venv/bin/uvicorn.
        full_cmd = ["bash", "-lc", " ".join(map(str, cmd))]
        proc = subprocess.run(
            full_cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,  # límite razonable para una demo
        )
        output = proc.stdout or ""
        if proc.stderr:
            # Incluir stderr al final ayuda mucho a depurar sin romper la UX.
            output += "\n\n[stderr]\n" + proc.stderr
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "La simulación tardó demasiado y se canceló (timeout)."
    except FileNotFoundError:
        return 1, (
            "No se encontró el comando `miniverse`. "
            "Asegurate de estar ejecutando este servidor dentro del entorno virtual donde instalaste Miniverse."
        )
    except Exception as e:  # pragma: no cover - ruta de error genérico
        return 1, f"Error inesperado al ejecutar Miniverse: {e}"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """
    Página principal: formulario HTML muy simple para elegir escenario y ticks.
    """
    options_html = "\n".join(
        f'<option value="{html.escape(key)}">{html.escape(label)} ({key})</option>'
        for key, label in SCENARIOS.items()
    )
    return f"""
    <!doctype html>
    <html lang="es">
    <head>
        <meta charset="utf-8" />
        <title>Miniverse Web Demo</title>
        <style>
            body {{
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                margin: 0;
                padding: 2rem;
                background: #0b1020;
                color: #f5f5f5;
            }}
            .container {{
                max-width: 960px;
                margin: 0 auto;
            }}
            h1 {{
                margin-bottom: 0.25rem;
            }}
            small {{
                color: #b0b3c0;
            }}
            label {{
                display: block;
                margin-top: 1rem;
                margin-bottom: 0.25rem;
                font-weight: 600;
            }}
            select, input[type="number"] {{
                padding: 0.4rem 0.6rem;
                border-radius: 4px;
                border: 1px solid #3a415f;
                background: #151a2c;
                color: #f5f5f5;
                min-width: 260px;
            }}
            button {{
                margin-top: 1.25rem;
                padding: 0.5rem 1rem;
                border-radius: 4px;
                border: none;
                background: #4f46e5;
                color: white;
                font-weight: 600;
                cursor: pointer;
            }}
            button:disabled {{
                opacity: 0.6;
                cursor: default;
            }}
            pre {{
                margin-top: 1.5rem;
                padding: 1rem;
                background: #050816;
                border-radius: 6px;
                border: 1px solid #222a3f;
                max-height: 480px;
                overflow: auto;
                font-size: 0.85rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Miniverse Web Demo</h1>
            <small>Ejecutá escenarios de Miniverse desde el navegador.</small>

            <form id="run-form">
                <label for="scenario">Escenario</label>
                <select id="scenario" name="scenario">
                    {options_html}
                </select>

                <label for="ticks">Ticks (pasos de simulación)</label>
                <input id="ticks" name="ticks" type="number" min="1" max="200" value="{DEFAULT_TICKS}" />

                <button type="submit" id="run-btn">Run</button>
            </form>

            <pre id="output">(salida de la simulación aparecerá aquí)</pre>
        </div>

        <script>
            const form = document.getElementById("run-form");
            const output = document.getElementById("output");
            const btn = document.getElementById("run-btn");

            form.addEventListener("submit", async (e) => {{
                e.preventDefault();
                btn.disabled = true;
                output.textContent = "Ejecutando simulación...";

                const formData = new FormData(form);
                try {{
                    const resp = await fetch("/run", {{
                        method: "POST",
                        body: formData
                    }});
                    const text = await resp.text();
                    output.textContent = text || "(sin salida)";
                }} catch (err) {{
                    output.textContent = "Error al llamar a /run: " + err;
                }} finally {{
                    btn.disabled = false;
                }}
            }});
        </script>
    </body>
    </html>
    """


@app.post("/run", response_class=PlainTextResponse)
async def run_scenario(
    scenario: str = Form(...),
    ticks: int = Form(DEFAULT_TICKS),
) -> str:
    """
    Endpoint que ejecuta un escenario concreto y devuelve la salida como texto plano.
    """
    try:
        ticks_int = int(ticks)
    except Exception:
        ticks_int = DEFAULT_TICKS
    if ticks_int <= 0:
        ticks_int = DEFAULT_TICKS

    code, output = _run_miniverse_cli(scenario, ticks_int)
    header = f"[escenario: {scenario} | ticks: {ticks_int} | exit_code: {code}]\n\n"
    return header + output

