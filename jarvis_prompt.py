"""
Carga del system prompt principal (agent_prompt.txt) más extensión opcional Jarbis Cripto.
Si existe JARBIS_CRIPTO_PROMPT.md o agent_prompt_jarbis_cripto.md con texto, se concatena al prompt.
"""

from pathlib import Path

_EXTRA_NAMES = ("JARBIS_CRIPTO_PROMPT.md", "agent_prompt_jarbis_cripto.md")

_CRIPTO_HEADER = "\n\n--- Jarbis Cripto (instrucciones adicionales) ---\n\n"


def read_jarbis_cripto_extension(base_dir: Path) -> str:
    for name in _EXTRA_NAMES:
        p = base_dir / name
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                return t
    return ""


def compose_agent_system_prompt(base_dir: Path, *, fallback_main: str | None = None) -> str:
    """
    Lee agent_prompt.txt si existe; si no, usa fallback_main (solo bridge sin archivo).
    Añade la extensión Jarbis Cripto si hay archivo .md con contenido.
    """
    main_path = base_dir / "agent_prompt.txt"
    if main_path.exists():
        main = main_path.read_text(encoding="utf-8").rstrip()
    elif fallback_main is not None:
        main = fallback_main.rstrip()
    else:
        main = ""
    extra = read_jarbis_cripto_extension(base_dir)
    if extra:
        main = main + _CRIPTO_HEADER + extra
    return main
