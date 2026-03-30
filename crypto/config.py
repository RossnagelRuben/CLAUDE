# Configuración Jarvis Cripto desde variables de entorno.

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Mints mainnet Solana (Jupiter)
MINT_SOL = "So11111111111111111111111111111111111111112"
MINT_USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
MINT_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Símbolo normalizado -> (mint, decimals)
KNOWN_SPL: dict[str, tuple[str, int]] = {
    "SOL": (MINT_SOL, 9),
    "USDT": (MINT_USDT, 6),
    "USDC": (MINT_USDC, 6),
}


@dataclass(frozen=True)
class CryptoConfig:
    coinmarketcap_api_key: str
    convert_currencies: str  # ej. USD,ARS
    listings_cache_ttl: float
    quote_cache_ttl: float
    jupiter_quote_url: str
    jupiter_swap_url: str
    solana_rpc_url: str | None
    default_slippage_bps: int
    data_dir: Path


def load_crypto_config(base_dir: Path) -> CryptoConfig:
    key = os.getenv("COINMARKETCAP_API_KEY", "").strip() or os.getenv("CMC_API_KEY", "").strip()
    convert_currencies = os.getenv("CRYPTO_CMC_CONVERT", "USD,ARS").strip() or "USD,ARS"
    listings_ttl = float(os.getenv("CRYPTO_LISTINGS_CACHE_TTL", "60") or "60")
    quote_ttl = float(os.getenv("CRYPTO_QUOTE_CACHE_TTL", "15") or "15")
    jq = os.getenv("JUPITER_QUOTE_API_URL", "https://quote-api.jup.ag/v6/quote").strip()
    js = os.getenv("JUPITER_SWAP_API_URL", "https://quote-api.jup.ag/v6/swap").strip()
    rpc = os.getenv("SOLANA_RPC_URL", "").strip() or None
    slip = int(os.getenv("CRYPTO_JUPITER_SLIPPAGE_BPS", "50") or "50")
    data_dir = Path(os.getenv("CRYPTO_DATA_DIR", "") or (base_dir / "agent_data" / "crypto"))
    return CryptoConfig(
        coinmarketcap_api_key=key,
        convert_currencies=convert_currencies,
        listings_cache_ttl=max(5.0, listings_ttl),
        quote_cache_ttl=max(3.0, quote_ttl),
        jupiter_quote_url=jq,
        jupiter_swap_url=js,
        solana_rpc_url=rpc,
        default_slippage_bps=max(1, min(slip, 500)),
        data_dir=data_dir,
    )
