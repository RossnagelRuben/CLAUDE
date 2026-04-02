# Detección de preguntas de precio en lenguaje natural (p. ej. chat web sin /cripto).

from __future__ import annotations

import re

# Debe sonar a consulta de precio/cotización, no a opinión general.
_PRICE_INTENT = re.compile(
    r"(?is)\b("
    r"precio|cotizaci[oó]n|cotiza|cu[aá]nto\s+vale|cu[aá]nto\s+est[aá]|"
    r"cu[aá]l\s+es\s+el\s+precio|valor\s+de|a\s+cu[aá]nto|"
    r"how\s+much\s+is|what'?s\s+the\s+price"
    r")\b"
)

# Nombres comunes → símbolo CMC (orden: nombres compuestos primero).
_NAME_TO_SYM: tuple[tuple[str, str], ...] = (
    ("binance coin", "BNB"),
    ("bitcoin cash", "BCH"),
    ("ethereum classic", "ETC"),
    ("solana", "SOL"),
    ("bitcoin", "BTC"),
    ("ethereum", "ETH"),
    ("dogecoin", "DOGE"),
    ("cardano", "ADA"),
    ("polkadot", "DOT"),
    ("avalanche", "AVAX"),
    ("chainlink", "LINK"),
    ("polygon", "MATIC"),
    ("litecoin", "LTC"),
    ("cosmos", "ATOM"),
    ("uniswap", "UNI"),
    ("stellar", "XLM"),
    ("monero", "XMR"),
    ("tron", "TRX"),
    ("shiba", "SHIB"),
    ("ripple", "XRP"),
    ("xrp", "XRP"),
    ("bnb", "BNB"),
)

_TICKER_WORD = re.compile(
    r"\b(BTC|ETH|SOL|DOGE|ADA|XRP|BNB|MATIC|DOT|AVAX|LINK|LTC|ATOM|UNI|SHIB|TRX)\b",
    re.IGNORECASE,
)


def _name_for_token(token: str) -> str | None:
    """Mapea una palabra suelta (ej. solana) a símbolo CMC."""
    tok = token.strip().lower()
    if not tok:
        return None
    for name, sym in _NAME_TO_SYM:
        if tok == name:
            return sym
    if tok == "sol":
        return "SOL"
    return None


def detect_crypto_price_intent(text: str) -> str | None:
    """
    Si el mensaje parece pedir el precio de una cripto conocida, devuelve el símbolo (ej. SOL).
    Si no aplica, None (sigue el flujo normal con IA).
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 400:
        return None
    # Copiar/pegar desde Markdown con backticks
    t = raw.lower()
    t = re.sub(r'[`´\'"\u2018\u2019\u201c\u201d]+', "", t)
    t = re.sub(r"\s+", " ", t).strip()

    # Frases cortas: "precio solana", "precio de solana", "precio bitcoin"
    m_direct = re.match(
        r"^\s*precio\s+(?:de\s+)?([a-z0-9áéíóúñ]{2,40})\s*$",
        t,
    )
    if m_direct:
        hit = _name_for_token(m_direct.group(1))
        if hit:
            return hit
        m_t = _TICKER_WORD.search(m_direct.group(1).upper())
        if m_t:
            return m_t.group(1).upper()

    if not _PRICE_INTENT.search(t):
        return None

    for name, sym in _NAME_TO_SYM:
        if re.search(r"\b" + re.escape(name) + r"\b", t):
            return sym

    # "precio de sol" / "cuánto vale sol" (ticker corto)
    if re.search(r"(?i)\bsol\b", t) and not re.search(r"(?i)\bsolana\b", t):
        return "SOL"

    m = _TICKER_WORD.search(t)
    if m:
        return m.group(1).upper()

    return None
