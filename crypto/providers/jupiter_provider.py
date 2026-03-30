# Jupiter v6 — cotización y armado de swap en Solana (sin firmar ni custodiar claves).

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from crypto.config import KNOWN_SPL, CryptoConfig
from crypto.interfaces import ITradingProvider
from crypto.models import PreparedTrade, QuoteResult, TradeExecutionResult

logger = logging.getLogger(__name__)


class JupiterTradingProvider(ITradingProvider):
    def __init__(self, config: CryptoConfig):
        self._cfg = config

    def _resolve_mint(self, token: str) -> tuple[str, int] | None:
        t = token.strip().upper()
        return KNOWN_SPL.get(t)

    def supports_pair(self, from_token: str, to_token: str) -> bool:
        return self._resolve_mint(from_token) is not None and self._resolve_mint(to_token) is not None

    def get_quote(
        self,
        from_token: str,
        to_token: str,
        amount: Decimal,
        *,
        slippage_bps: int = 50,
    ) -> QuoteResult:
        a = self._resolve_mint(from_token)
        b = self._resolve_mint(to_token)
        if not a or not b:
            raise ValueError(
                f"Par no soportado en Fase 1.5 (solo mints conocidos): {from_token} -> {to_token}. "
                f"Usá SOL, USDT o USDC."
            )
        in_mint, in_dec = a
        out_mint, out_dec = b
        if amount <= 0:
            raise ValueError("El monto debe ser mayor a cero.")
        scale = Decimal(10) ** in_dec
        atomic = int((amount.quantize(Decimal("1." + "0" * in_dec)) * scale))
        if atomic <= 0:
            raise ValueError("Monto demasiado pequeño para cotizar.")

        params = {
            "inputMint": in_mint,
            "outputMint": out_mint,
            "amount": str(atomic),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        q = urllib.parse.urlencode(params)
        url = f"{self._cfg.jupiter_quote_url.rstrip('/')}?{q}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_txt = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Jupiter quote HTTP {e.code}: {err}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Jupiter sin conexión: {e}") from e

        raw = json.loads(raw_txt)
        if not isinstance(raw, dict):
            raise RuntimeError("Respuesta Jupiter inválida")

        in_amt = int(raw.get("inAmount") or atomic)
        out_amt = int(raw.get("outAmount") or 0)
        pip = raw.get("priceImpactPct")
        pip_s = str(pip) if pip is not None else None

        return QuoteResult(
            provider="jupiter_v6",
            input_mint=in_mint,
            output_mint=out_mint,
            input_symbol=from_token.strip().upper(),
            output_symbol=to_token.strip().upper(),
            in_amount_atomic=in_amt,
            out_amount_atomic=out_amt,
            input_decimals=in_dec,
            output_decimals=out_dec,
            price_impact_pct=pip_s,
            raw=raw,
        )

    def build_swap_transaction(self, quote: QuoteResult, user_public_key: str) -> PreparedTrade:
        pk = (user_public_key or "").strip()
        if len(pk) < 32:
            raise ValueError("Public key Solana inválida. Usá /cripto wallet <tu_pubkey_base58>.")

        body = json.dumps(
            {
                "quoteResponse": quote.raw,
                "userPublicKey": pk,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto",
            }
        ).encode("utf-8")
        url = self._cfg.jupiter_swap_url.rstrip("/")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                swap_raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Jupiter swap HTTP {e.code}: {err}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Jupiter sin conexión: {e}") from e

        if not isinstance(swap_raw, dict):
            raise RuntimeError("Respuesta swap Jupiter inválida")
        b64 = swap_raw.get("swapTransaction")
        if not b64 or not isinstance(b64, str):
            raise RuntimeError("Jupiter no devolvió swapTransaction (cotización expirada o error).")

        sim_logs: str | None = None
        if self._cfg.solana_rpc_url:
            sim_logs = self._simulate_transaction(b64)

        return PreparedTrade(
            provider=quote.provider,
            user_public_key=pk,
            swap_transaction_base64=b64,
            quote=quote,
            simulation_logs=sim_logs,
        )

    def _simulate_transaction(self, swap_tx_b64: str) -> str | None:
        rpc = self._cfg.solana_rpc_url
        if not rpc:
            return None
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "simulateTransaction",
                "params": [
                    swap_tx_b64,
                    {"encoding": "base64", "commitment": "processed"},
                ],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            rpc,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.info("simulateTransaction omitido: %s", e)
            return None
        if not isinstance(out, dict):
            return None
        res = out.get("result") or {}
        if isinstance(res, dict):
            err = res.get("err")
            logs = res.get("logs")
            if err:
                return f"err={err} logs_head={(logs or [])[:5]}"
            return "simulación OK (sin err)"
        return str(out)[:300]

    def execute_transaction(
        self,
        prepared: PreparedTrade,
        *,
        confirm_user_ack: bool = False,
        rpc_url: str | None = None,
    ) -> TradeExecutionResult:
        """
        No se envían transacciones firmadas desde el servidor (sin clave privada).
        Con confirmación explícita solo registramos intención y recordamos el flujo seguro.
        """
        if not confirm_user_ack:
            return TradeExecutionResult(
                success=False,
                message="Operación no ejecutada: se requiere confirmación explícita del usuario.",
                error="missing_confirmation",
            )
        return TradeExecutionResult(
            success=False,
            message=(
                "No se ejecuta swap en cadena desde Jarvis: no almacenamos claves privadas. "
                "Usá la transacción en base64 con Phantom/Solflare u otra wallet (pegar o escanear)."
            ),
            error="execution_delegated_to_wallet",
        )
