# Jarvis Cripto — módulo desacoplado (mercado, historial, simulación, Jupiter).

from __future__ import annotations

from pathlib import Path

from crypto.cache import TtlCache
from crypto.commands import try_handle_crypto_command
from crypto.config import load_crypto_config
from crypto.providers.coinmarketcap_provider import CoinMarketCapProvider
from crypto.providers.jupiter_provider import JupiterTradingProvider
from crypto.service import CryptoService
from crypto.storage import JsonQueryHistoryStore, JsonSimulatedPortfolioStore

__all__ = [
    "build_crypto_service",
    "try_handle_crypto_command",
    "CryptoService",
]


def build_crypto_service(base_dir: Path) -> CryptoService:
    """
    Ensambla servicio con CMC, caché, persistencia y Jupiter (si aplica).
    """
    cfg = load_crypto_config(base_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cache = TtlCache()
    market = CoinMarketCapProvider(cfg, cache=cache)
    history = JsonQueryHistoryStore(cfg.data_dir / "history")
    portfolio = JsonSimulatedPortfolioStore(cfg.data_dir / "portfolio")
    trading = JupiterTradingProvider(cfg)
    return CryptoService(
        market,
        history,
        portfolio,
        trading=trading,
        default_slippage_bps=cfg.default_slippage_bps,
    )
