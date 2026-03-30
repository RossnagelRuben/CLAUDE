# Casos de uso Jarvis Cripto: orquesta mercado, historial, portfolio y Jupiter.

from __future__ import annotations

import logging
from decimal import Decimal

from crypto.formatter import (
    format_history,
    format_prepared_trade,
    format_price_quote,
    format_quote_result,
    format_simulated_balance,
    format_top_list,
)
from crypto.interfaces import IMarketDataProvider, IQueryHistoryStore, ISimulatedPortfolioStore, ITradingProvider
from crypto.models import PreparedTrade, QueryHistoryEntry, QuoteResult, TradeExecutionResult
from crypto.storage import utc_now_iso

logger = logging.getLogger(__name__)


class CryptoService:
    def __init__(
        self,
        market: IMarketDataProvider,
        history: IQueryHistoryStore,
        portfolio: ISimulatedPortfolioStore,
        trading: ITradingProvider | None,
        *,
        default_slippage_bps: int = 50,
    ):
        self._market = market
        self._history = history
        self._portfolio = portfolio
        self._trading = trading
        self._slippage = default_slippage_bps
        self._last_quote: dict[str, QuoteResult] = {}
        self._last_prepared: dict[str, PreparedTrade] = {}
        self._user_wallet: dict[str, str] = {}

    def _log(self, user_id: str, action: str, detail: str, meta: dict | None = None) -> None:
        self._history.append(
            QueryHistoryEntry(
                ts_iso=utc_now_iso(),
                user_id=user_id,
                action=action,
                detail=detail,
                meta=meta or {},
            )
        )

    def set_wallet(self, user_id: str, pubkey: str) -> str:
        pk = pubkey.strip()
        if len(pk) < 32 or len(pk) > 50:
            return "❌ Public key inválida (Base58 típico 32-44 caracteres)."
        self._user_wallet[user_id] = pk
        self._log(user_id, "wallet_set", pk[:12] + "…")
        return f"✅ Wallet registrada para swaps: {pk[:8]}…{pk[-6:]}"

    def get_wallet(self, user_id: str) -> str | None:
        return self._user_wallet.get(user_id)

    def price_for_user(self, user_id: str, symbol: str) -> str:
        sym = symbol.strip().upper()
        if not sym:
            return "❌ Indicá un símbolo, ej: /cripto precio SOL"
        try:
            quotes = self._market.quotes_latest([sym])
        except Exception as e:
            logger.warning("CMC quotes error: %s", e)
            return f"❌ No se pudo obtener precio: {e}"
        q = quotes.get(sym)
        if not q:
            return f"❌ Símbolo no encontrado en CMC: {sym}"
        self._log(user_id, "precio", sym)
        return format_price_quote(q)

    def top_for_user(self, user_id: str, limit: int) -> str:
        try:
            rows = self._market.listings_top(limit)
        except Exception as e:
            logger.warning("CMC listings error: %s", e)
            return f"❌ No se pudo obtener el ranking: {e}"
        extra: list = []
        try:
            sol_usdt = self._market.quotes_latest(["SOL", "USDT"])
            extra = [sol_usdt[k] for k in ("SOL", "USDT") if k in sol_usdt]
        except Exception:
            pass
        self._log(user_id, "top", f"limit={limit}")
        return format_top_list(rows, extra or None)

    def history_for_user(self, user_id: str, limit: int) -> str:
        entries = self._history.list_recent(user_id, max(1, min(limit, 100)))
        return format_history(entries)

    def balance_for_user(self, user_id: str) -> str:
        h = self._portfolio.get_holdings(user_id)
        return format_simulated_balance(h)

    def sim_set(self, user_id: str, symbol: str, amount: Decimal) -> str:
        sym = symbol.strip().upper()
        self._portfolio.set_holding(user_id, sym, amount)
        self._log(user_id, "sim_set", f"{sym}={amount}")
        return f"✅ {sym} = {amount} (simulado)\n\n" + self.balance_for_user(user_id)

    def sim_add(self, user_id: str, symbol: str, delta: Decimal) -> str:
        sym = symbol.strip().upper()
        self._portfolio.add_holding(user_id, sym, delta)
        self._log(user_id, "sim_add", f"{sym}+{delta}")
        return f"✅ Sumado {delta} {sym}\n\n" + self.balance_for_user(user_id)

    def sim_reset(self, user_id: str) -> str:
        self._portfolio.reset(user_id)
        self._last_quote.pop(user_id, None)
        self._last_prepared.pop(user_id, None)
        self._log(user_id, "sim_reset", "")
        return "✅ Balance simulado e intenciones swap locales reiniciados."

    def swap_quote(self, user_id: str, from_t: str, to_t: str, amount: Decimal) -> str:
        if not self._trading:
            return "❌ Trading Jupiter no disponible en esta instancia."
        try:
            q = self._trading.get_quote(from_t, to_t, amount, slippage_bps=self._slippage)
        except Exception as e:
            logger.warning("Jupiter quote error: %s", e)
            return f"❌ Cotización: {e}"
        self._last_quote[user_id] = q
        self._last_prepared.pop(user_id, None)
        self._log(user_id, "swap_quote", f"{from_t}->{to_t} amt={amount}")
        return format_quote_result(q)

    def swap_build(self, user_id: str) -> str:
        if not self._trading:
            return "❌ Trading Jupiter no disponible."
        q = self._last_quote.get(user_id)
        if not q:
            return "❌ No hay cotización previa. Usá /cripto swap quote SOL USDT 1"
        pk = self.get_wallet(user_id)
        if not pk:
            return "❌ Definí primero /cripto wallet <tu_pubkey_solana>"
        try:
            p = self._trading.build_swap_transaction(q, pk)
        except Exception as e:
            logger.warning("Jupiter build error: %s", e)
            return f"❌ Armar transacción: {e}"
        self._last_prepared[user_id] = p
        self._log(user_id, "swap_build", f"{q.input_symbol}->{q.output_symbol}")
        return format_prepared_trade(p)

    def peek_last_prepared(self, user_id: str) -> PreparedTrade | None:
        """Última transacción armada tras swap build (p. ej. adjuntar .b64 en Telegram)."""
        return self._last_prepared.get(user_id)

    def swap_confirm(self, user_id: str, yes: bool) -> str:
        if not self._trading:
            return "❌ Trading no disponible."
        prepared = self._last_prepared.get(user_id)
        if not prepared:
            return "❌ No hay transacción preparada. Flujo: quote → build → confirmar."
        if not yes:
            self._last_prepared.pop(user_id, None)
            self._log(user_id, "swap_cancel", "user_no")
            return "❌ Intención de swap cancelada (no se ejecutó nada en cadena)."
        res: TradeExecutionResult = self._trading.execute_transaction(
            prepared,
            confirm_user_ack=True,
            rpc_url=None,
        )
        self._log(
            user_id,
            "swap_confirm_ack",
            "user_yes",
            meta={"message": res.message, "success": res.success},
        )
        return (
            f"✅ Confirmación registrada.\n\n{res.message}\n\n"
            f"La firma y el envío los hacés vos en la wallet; Jarvis no custodia claves."
        )
