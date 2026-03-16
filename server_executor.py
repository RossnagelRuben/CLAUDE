"""
Ejecutor de comandos en el servidor (principio de responsabilidad única).
Usado por el bot para /confirm y /estado. Permite inyectar otro ejecutor en tests.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class CommandResult:
    """Resultado de ejecutar un comando: salida y si hubo error."""
    output: str
    success: bool
    hint: str | None = None  # Sugerencia cuando falla (ej. usar Docker para N8N)


def _hint_on_failure(command: str, output: str) -> str | None:
    """
    Si el fallo sugiere que falta npm/node/n8n, devuelve una pista para usar Docker.
    Así el asistente se autoperfecciona: el usuario ve la alternativa sin cambiar el flujo.
    """
    out_lower = (output or "").lower()
    cmd_lower = (command or "").lower()
    if "not found" not in out_lower and "no such file" not in out_lower:
        return None
    if "npm" in cmd_lower or "npx" in cmd_lower or "n8n" in cmd_lower or "node" in cmd_lower:
        return (
            "En este servidor no hay Node/npm en el PATH. "
            "Para N8N usá Docker: docker run -d --name n8n -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n"
        )
    return None


class CommandExecutor(Protocol):
    """Contrato para ejecutar comandos (principio de inversión de dependencias)."""

    def run(self, command: str, timeout_seconds: int = 120) -> CommandResult:
        ...


class ServerCommandExecutor:
    """Ejecuta comandos en el servidor vía subprocess. Implementación por defecto."""

    def run(self, command: str, timeout_seconds: int = 120) -> CommandResult:
        try:
            result = subprocess.run(
                command,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip()[:4000] or "(sin salida)"
            success = result.returncode == 0
            hint = None if success else _hint_on_failure(command, output)
            return CommandResult(output=output, success=success, hint=hint)
        except subprocess.TimeoutExpired:
            return CommandResult(
                output="Comando superó el tiempo límite.",
                success=False,
                hint=None,
            )
        except Exception as e:
            err = str(e)
            return CommandResult(
                output=err,
                success=False,
                hint=_hint_on_failure(command, err),
            )


# Instancia por defecto para inyección en el bot
default_executor: CommandExecutor = ServerCommandExecutor()
