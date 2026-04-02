# Texto listo para Telegram / WhatsApp (sin Markdown frágil).

from __future__ import annotations

from decimal import Decimal

from typing import Any

from crypto.models import PriceQuote, QueryHistoryEntry, QuoteResult, TopCryptoRow

# Degradados para tarjeta web (identidad aproximada por símbolo).
_SYMBOL_WEB_GRADIENT: dict[str, tuple[str, str]] = {
    "SOL": ("#9945FF", "#14F195"),
    "BTC": ("#F7931A", "#4a2800"),
    "ETH": ("#627EEA", "#1a1a2e"),
    "DOGE": ("#c2a633", "#2d1f00"),
    "ADA": ("#0033ad", "#00a8ff"),
    "XRP": ("#23292f", "#00aae4"),
    "BNB": ("#f0b90b", "#1e1500"),
    "MATIC": ("#8247e5", "#0f0518"),
    "DOT": ("#e6007a", "#1a0a14"),
    "AVAX": ("#e84142", "#1a0505"),
    "LINK": ("#375bd2", "#0a1020"),
}


def _fmt_money(d: Decimal, currency: str) -> str:
    if currency == "USD":
        if d >= Decimal("1"):
            return f"${d:,.2f}"
        return f"${d:.6f}".rstrip("0").rstrip(".")
    if d >= Decimal("1"):
        return f"ARS {d:,.2f}"
    return f"ARS {d:.4f}".rstrip("0").rstrip(".").rstrip(",")


def price_quote_to_web_card(q: PriceQuote) -> dict[str, Any]:
    """
    Payload JSON-serializable para la tarjeta de cotización en jarvis_web_chat.html.
    """
    gf, gt = _SYMBOL_WEB_GRADIENT.get(q.symbol, ("#2563eb", "#7c3aed"))
    ars = _fmt_money(q.price_ars, "ARS") if q.price_ars and q.price_ars > 0 else None
    out: dict[str, Any] = {
        "type": "crypto_quote",
        "symbol": q.symbol,
        "name": q.name,
        "price_usd": _fmt_money(q.price_usd, "USD"),
        "price_ars": ars,
        "has_ars": bool(ars),
        "gradient_from": gf,
        "gradient_to": gt,
    }
    if q.percent_change_24h is not None:
        ch = float(q.percent_change_24h)
        out["change_24h"] = ch
        out["change_label"] = f"{ch:+.2f}%"
        out["change_up"] = ch >= 0
    else:
        out["change_24h"] = None
        out["change_label"] = None
        out["change_up"] = None
    if q.market_cap_usd is not None and q.market_cap_usd > 0:
        out["market_cap_usd"] = _fmt_money(q.market_cap_usd, "USD")
    else:
        out["market_cap_usd"] = None
    out["rank"] = int(q.rank) if q.rank else None
    return out


def format_price_quote(q: PriceQuote) -> str:
    ars_line = f"   ARS: {_fmt_money(q.price_ars, 'ARS')}"
    if q.price_ars <= 0:
        ars_line = "   ARS: (sin cotización — revisá convert=ARS en CMC o tu plan de API)"
    lines = [
        f"💹 {q.name} ({q.symbol})",
        f"   USD: {_fmt_money(q.price_usd, 'USD')}",
        ars_line,
    ]
    if q.percent_change_24h is not None:
        sign = "+" if q.percent_change_24h >= 0 else ""
        lines.append(f"   24h: {sign}{q.percent_change_24h:.2f}%")
    if q.market_cap_usd is not None and q.market_cap_usd > 0:
        lines.append(f"   Cap. mercado: {_fmt_money(q.market_cap_usd, 'USD')}")
    if q.rank:
        lines.append(f"   Rank CMC: #{q.rank}")
    return "\n".join(lines)


def format_top_list(rows: list[TopCryptoRow], extra_sol_usdt: list[PriceQuote] | None = None) -> str:
    lines = ["🏆 Top cripto (por cap. mercado, CMC):", ""]
    for r in rows:
        chg = ""
        if r.percent_change_24h is not None:
            sign = "+" if r.percent_change_24h >= 0 else ""
            chg = f" ({sign}{r.percent_change_24h:.2f}% 24h)"
        lines.append(
            f"{r.rank}. {r.symbol} — {_fmt_money(r.price_usd, 'USD')} | "
            f"{_fmt_money(r.price_ars, 'ARS')}{chg}"
        )
    if extra_sol_usdt:
        lines.append("")
        lines.append("📌 Referencia SOL / USDT:")
        for q in extra_sol_usdt:
            for pline in format_price_quote(q).splitlines():
                lines.append("   " + pline)
    return "\n".join(lines)


def format_history(entries: list[QueryHistoryEntry]) -> str:
    if not entries:
        return "📜 No hay consultas recientes registradas."
    lines = ["📜 Últimas consultas:", ""]
    for e in entries:
        lines.append(f"• {e.ts_iso} — {e.action}: {e.detail}")
    return "\n".join(lines)


def format_simulated_balance(holdings: dict[str, Decimal]) -> str:
    if not holdings:
        return (
            "💼 Balance simulado: vacío.\n"
            "Usá: /cripto sim set SOL 1 o /cripto sim add USDT 100"
        )
    lines = ["💼 Balance simulado:", ""]
    for sym in sorted(holdings.keys()):
        lines.append(f"   {sym}: {holdings[sym]:,.8f}".rstrip("0").rstrip(".").rstrip(","))
    lines.append("")
    lines.append("(Simulación local; no es saldo on-chain ni de exchange.)")
    return "\n".join(lines)


def format_quote_result(q: QuoteResult) -> str:
    in_h = Decimal(q.in_amount_atomic) / (Decimal(10) ** q.input_decimals)
    out_h = Decimal(q.out_amount_atomic) / (Decimal(10) ** q.output_decimals)
    pip = q.price_impact_pct or "n/d"
    return (
        f"🔁 Cotización Jupiter ({q.input_symbol} → {q.output_symbol})\n"
        f"   Entrada: {in_h} {q.input_symbol}\n"
        f"   Salida estimada: {out_h} {q.output_symbol}\n"
        f"   Impacto precio: {pip}\n"
        f"\n"
        f"Siguiente: /cripto build (requiere /cripto wallet <pubkey>)\n"
        f"Recordá: sin confirmación no hay pasos de ejecución automática."
    )


def format_prepared_trade(p: PreparedTrade) -> str:
    head = (
        f"🧾 Swap preparado (sin firmar)\n"
        f"   Par: {p.quote.input_symbol} → {p.quote.output_symbol}\n"
        f"   Wallet: {p.user_public_key[:8]}…{p.user_public_key[-6:]}\n"
    )
    if p.simulation_logs:
        head += f"   Simulación RPC: {p.simulation_logs}\n"
    head += (
        f"\nTransacción (base64, primeros 80 chars):\n{p.swap_transaction_base64[:80]}…\n\n"
        f"Copiá el base64 completo desde el JSON interno o repetí /cripto build y guardá el mensaje.\n"
        f"Para confirmar el flujo seguro (registro): /cripto swap confirmar SI\n"
        f"Cancelar intención: /cripto swap confirmar NO"
    )
    return head


def help_text() -> str:
    return (
        "🪙 Jarvis Cripto — Fase 1 / 1.5\n\n"
        "Precios (CoinMarketCap + USD/ARS):\n"
        "  /cripto precio BTC\n"
        "  /cripto top 10\n"
        "  /cripto historial 15\n"
        "  /cripto balance\n"
        "  /cripto sim set SOL 2.5\n"
        "  /cripto sim add USDT 50\n"
        "  /cripto sim reset\n\n"
        "Swap Solana (Jupiter, sin auto-ejecución):\n"
        "  /cripto wallet <pubkey_base58>\n"
        "  /cripto swap quote SOL USDT 1\n"
        "  /cripto swap build\n"
        "  /cripto swap confirmar SI | NO\n\n"
        "Variables .env: COINMARKETCAP_API_KEY (o CMC_API_KEY), "
        "opcional SOLANA_RPC_URL para simular la tx.\n\n"
        "No somos asesores financieros; no hay trading real desde el bot."
    )
