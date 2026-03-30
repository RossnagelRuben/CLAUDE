"""
Bloque de fecha/hora actual para system prompts (siempre formato 24 h).
"""

from __future__ import annotations

from datetime import datetime, timezone


def format_datetime_context_for_system_prompt() -> str:
    """Texto corto para anexar al system instruction (hora local del servidor + UTC)."""
    local = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    loc_s = local.strftime("%Y-%m-%d %H:%M")
    utc_s = utc.strftime("%Y-%m-%d %H:%M")
    tz_label = local.tzname() or "local"
    return (
        "\n\n--- FECHA Y HORA ACTUAL (formato 24 h) ---\n"
        f"Hora local del servidor: {loc_s} ({tz_label})\n"
        f"UTC: {utc_s}\n"
        "Usá estas marcas como referencia para «hoy», «mañana» y citas. "
        "Al hablar con el usuario, las horas siempre en 24 h (HH:mm)."
    )
