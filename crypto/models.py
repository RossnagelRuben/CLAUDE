# Modelos de dominio Jarvis Cripto (precios, historial, portfolio simulado, trading preparado).

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PriceQuote:
    """Cotización de un activo en USD y ARS."""

    symbol: str
    name: str
    price_usd: Decimal
    price_ars: Decimal
    percent_change_24h: Decimal | None = None
    market_cap_usd: Decimal | None = None
    rank: int | None = None
    last_updated: str | None = None


@dataclass(frozen=True)
class TopCryptoRow:
    """Fila del ranking (listings)."""

    rank: int
    symbol: str
    name: str
    price_usd: Decimal
    price_ars: Decimal
    percent_change_24h: Decimal | None


@dataclass
class QueryHistoryEntry:
    """Entrada de historial de consultas del usuario."""

    ts_iso: str
    user_id: str
    action: str
    detail: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulatedHolding:
    symbol: str
    amount: Decimal


@dataclass(frozen=True)
class QuoteResult:
    """
    Cotización de swap (Jupiter u otro proveedor).
    `raw` conserva el JSON del proveedor para build_swap_transaction.
    """

    provider: str
    input_mint: str
    output_mint: str
    input_symbol: str
    output_symbol: str
    in_amount_atomic: int
    out_amount_atomic: int
    input_decimals: int
    output_decimals: int
    price_impact_pct: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PreparedTrade:
    """Transacción armada; el usuario debe firmar en wallet (no hay claves en servidor)."""

    provider: str
    user_public_key: str
    swap_transaction_base64: str
    quote: QuoteResult
    simulation_logs: str | None = None


@dataclass(frozen=True)
class TradeExecutionResult:
    """Resultado de intento de ejecución (en servidor solo simulación o rechazo)."""

    success: bool
    message: str
    signature: str | None = None
    error: str | None = None
