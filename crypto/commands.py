# Parsing de comandos de texto (Telegram / WhatsApp) → CryptoService.

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from crypto.formatter import help_text
from crypto.service import CryptoService


def _norm_cmd(text: str) -> str:
    t = (text or "").strip()
    if t.lower().startswith("/cripto"):
        return t
    if re.match(r"^cripto\b", t, flags=re.IGNORECASE):
        return "/" + t
    return t


def sanitize_crypto_symbol_token(raw: str) -> str:
    """
    CoinMarketCap exige símbolos alfanuméricos; al copiar desde Markdown a veces
    queda un backtick u otro carácter (p. ej. SOL`) y la API devuelve 400.
    """
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r'[`´\'"\u2018\u2019\u201c\u201d]+', "", s)
    s = re.sub(r"[^A-Za-z0-9]", "", s)
    return s.upper()


def web_price_command_symbol(text: str) -> str | None:
    """
    Si el mensaje es explícitamente «/cripto precio SYM» (o «cripto precio SYM»), devuelve SYM.
    Usado por el chat web para mostrar tarjeta sin duplicar lógica.
    """
    raw = _norm_cmd(text)
    if not raw.lower().startswith("/cripto"):
        return None
    parts = raw[len("/cripto") :].strip().split()
    if len(parts) >= 2 and parts[0].lower() == "precio":
        sym = sanitize_crypto_symbol_token(parts[1])
        return sym if sym else None
    return None


def _decimal(s: str) -> Decimal:
    try:
        return Decimal(s.strip().replace(",", "."))
    except InvalidOperation as e:
        raise ValueError(f"Monto inválido: {s}") from e


def try_handle_crypto_command(text: str, user_id: str, service: CryptoService) -> str | None:
    """
    Si el mensaje es un comando /cripto, devuelve la respuesta.
    Si no aplica, devuelve None (el caller sigue con IA u otros handlers).
    """
    raw = _norm_cmd(text)
    if not raw.lower().startswith("/cripto"):
        return None

    rest = raw[len("/cripto") :].strip()
    if not rest:
        return help_text()

    parts = rest.split()
    head = parts[0].lower()
    if head in ("help", "ayuda", "?"):
        return help_text()

    if head == "precio" and len(parts) >= 2:
        sym = sanitize_crypto_symbol_token(parts[1])
        if not sym:
            return "❌ Símbolo inválido (solo letras y números, ej: SOL)."
        return service.price_for_user(user_id, sym)

    if head == "top":
        n = 10
        if len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
        return service.top_for_user(user_id, n)

    if head == "historial":
        n = 20
        if len(parts) >= 2 and parts[1].isdigit():
            n = int(parts[1])
        return service.history_for_user(user_id, n)

    if head == "balance":
        return service.balance_for_user(user_id)

    if head == "wallet" and len(parts) >= 2:
        return service.set_wallet(user_id, parts[1])

    if head == "sim":
        if len(parts) >= 2 and parts[1].lower() == "reset":
            return service.sim_reset(user_id)
        if len(parts) >= 4 and parts[1].lower() == "set":
            sym, amt_s = parts[2], parts[3]
            return service.sim_set(user_id, sym, _decimal(amt_s))
        if len(parts) >= 4 and parts[1].lower() == "add":
            sym, amt_s = parts[2], parts[3]
            return service.sim_add(user_id, sym, _decimal(amt_s))
        return (
            "Uso: /cripto sim set SOL 1 | /cripto sim add USDT 10 | /cripto sim reset"
        )

    if head == "swap":
        sub = parts[1].lower() if len(parts) >= 2 else ""
        if sub == "quote" and len(parts) >= 5:
            from_t, to_t, amt_s = parts[2], parts[3], parts[4]
            return service.swap_quote(user_id, from_t, to_t, _decimal(amt_s))
        if sub == "build":
            return service.swap_build(user_id)
        if sub == "confirmar" and len(parts) >= 3:
            ans = parts[2].upper()
            yes = ans in ("SI", "SÍ", "YES", "Y", "S")
            no = ans in ("NO", "N", "CANCEL", "CANCELAR")
            if yes:
                return service.swap_confirm(user_id, True)
            if no:
                return service.swap_confirm(user_id, False)
            return "Usá: /cripto swap confirmar SI o NO"
        return (
            "Swap: /cripto swap quote SOL USDT 1 → /cripto wallet <pk> → "
            "/cripto swap build → /cripto swap confirmar SI|NO"
        )

    return (
        "Comando /cripto no reconocido. Probá /cripto ayuda"
    )
