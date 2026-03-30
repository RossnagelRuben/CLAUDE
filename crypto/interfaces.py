# Protocolos para inversión de dependencias (mismo estilo que drr/).

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from crypto.models import PreparedTrade, PriceQuote, QueryHistoryEntry, QuoteResult, TopCryptoRow, TradeExecutionResult


@runtime_checkable
class IMarketDataProvider(Protocol):
    """Proveedor de precios y ranking (p. ej. CoinMarketCap)."""

    def quotes_latest(self, symbols: list[str]) -> dict[str, PriceQuote]:
        """Clave normalizada upper (BTC, SOL, USDT)."""
        ...

    def listings_top(self, limit: int) -> list[TopCryptoRow]:
        ...


@runtime_checkable
class IQueryHistoryStore(Protocol):
    def append(self, entry: QueryHistoryEntry) -> None:
        ...

    def list_recent(self, user_id: str, limit: int) -> list[QueryHistoryEntry]:
        ...


@runtime_checkable
class ISimulatedPortfolioStore(Protocol):
    def get_holdings(self, user_id: str) -> dict[str, Decimal]:
        """Símbolo upper -> cantidad."""
        ...

    def set_holding(self, user_id: str, symbol: str, amount: Decimal) -> None:
        ...

    def add_holding(self, user_id: str, symbol: str, delta: Decimal) -> None:
        ...

    def reset(self, user_id: str) -> None:
        ...


@runtime_checkable
class ITradingProvider(Protocol):
    """
    Proveedor de swap desacoplado (Jupiter, futuro CEX/DEX).
    No almacena claves privadas; execute puede simular o rechazar.
    """

    def get_quote(
        self,
        from_token: str,
        to_token: str,
        amount: Decimal,
        *,
        slippage_bps: int = 50,
    ) -> QuoteResult:
        ...

    def build_swap_transaction(self, quote: QuoteResult, user_public_key: str) -> PreparedTrade:
        ...

    def execute_transaction(
        self,
        prepared: PreparedTrade,
        *,
        confirm_user_ack: bool = False,
        rpc_url: str | None = None,
    ) -> TradeExecutionResult:
        ...

    def supports_pair(self, from_token: str, to_token: str) -> bool:
        ...


@runtime_checkable
class IResponseCache(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        ...
