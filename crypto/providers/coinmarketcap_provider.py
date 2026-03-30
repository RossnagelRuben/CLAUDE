# CoinMarketCap — datos de mercado (requiere API key).

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from crypto.config import CryptoConfig
from crypto.interfaces import IMarketDataProvider, IResponseCache
from crypto.models import PriceQuote, TopCryptoRow

logger = logging.getLogger(__name__)

CMC_BASE = "https://pro-api.coinmarketcap.com/v1"


def _parse_convert_currencies(convert_csv: str) -> list[str]:
    """Lista de símbolos fiat/crypto para convert (ej. USD, ARS). Sin duplicados."""
    out: list[str] = []
    seen: set[str] = set()
    for part in (convert_csv or "USD").split(","):
        p = part.strip().upper()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out or ["USD"]


class CoinMarketCapProvider(IMarketDataProvider):
    def __init__(self, config: CryptoConfig, cache: IResponseCache | None = None):
        self._cfg = config
        self._cache = cache

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-CMC_PRO_API_KEY": self._cfg.coinmarketcap_api_key,
        }

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        if not self._cfg.coinmarketcap_api_key:
            raise RuntimeError("Falta COINMARKETCAP_API_KEY o CMC_API_KEY en .env")
        q = urllib.parse.urlencode(params)
        url = f"{CMC_BASE}{path}?{q}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:800]
            logger.warning("CMC HTTPError %s: %s", e.code, err)
            detail = err
            try:
                ej = json.loads(err)
                if isinstance(ej, dict):
                    st = ej.get("status") or {}
                    detail = str(st.get("error_message") or st.get("error_code") or err)[:400]
            except Exception:
                pass
            raise RuntimeError(f"CoinMarketCap error HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"CoinMarketCap sin conexión: {e}") from e
        data = json.loads(body)
        if isinstance(data, dict) and data.get("status", {}).get("error_code"):
            msg = data.get("status", {}).get("error_message", "error")
            raise RuntimeError(f"CoinMarketCap: {msg}")
        if not isinstance(data, dict):
            raise RuntimeError("Respuesta CMC inválida")
        return data

    def quotes_latest(self, symbols: list[str]) -> dict[str, PriceQuote]:
        if not symbols:
            return {}
        syms = sorted({s.strip().upper() for s in symbols if s.strip()})
        if not syms:
            return {}
        cache_key = f"cmc:quotes:{','.join(syms)}:{self._cfg.convert_currencies}"
        if self._cache:
            hit = self._cache.get(cache_key)
            if isinstance(hit, dict):
                return hit

        # Muchos planes (p. ej. Basic) solo permiten 1 valor en `convert` por llamada;
        # `convert=USD,ARS` devuelve HTTP 400. Hacemos una petición por moneda y fusionamos quote.
        converts = _parse_convert_currencies(self._cfg.convert_currencies)
        merged: dict[str, dict] = {}
        for conv in converts:
            params = {"symbol": ",".join(syms), "convert": conv}
            raw = self._get_json("/cryptocurrency/quotes/latest", params)
            data = raw.get("data") or {}
            if not isinstance(data, dict):
                continue
            for sym, payload in data.items():
                if not isinstance(payload, dict):
                    continue
                sym_u = str(sym).upper()
                if sym_u not in merged:
                    merged[sym_u] = dict(payload)
                    merged[sym_u]["quote"] = {}
                q = payload.get("quote") or {}
                if conv in q:
                    merged[sym_u]["quote"][conv] = q[conv]
        data = merged
        out: dict[str, PriceQuote] = {}
        if not isinstance(data, dict):
            return out
        for sym, payload in data.items():
            if not isinstance(payload, dict):
                continue
            sym_u = str(sym).upper()
            quote = payload.get("quote") or {}
            usd = quote.get("USD") or {}
            ars = quote.get("ARS") or {}
            try:
                price_usd = Decimal(str(usd.get("price") or "0"))
            except Exception:
                price_usd = Decimal("0")
            try:
                price_ars = Decimal(str(ars.get("price") or "0"))
            except Exception:
                price_ars = Decimal("0")
            chg = usd.get("percent_change_24h")
            try:
                chg_d = Decimal(str(chg)) if chg is not None else None
            except Exception:
                chg_d = None
            mcap = usd.get("market_cap")
            try:
                mcap_d = Decimal(str(mcap)) if mcap is not None else None
            except Exception:
                mcap_d = None
            out[sym_u] = PriceQuote(
                symbol=sym_u,
                name=str(payload.get("name") or sym_u),
                price_usd=price_usd,
                price_ars=price_ars,
                percent_change_24h=chg_d,
                market_cap_usd=mcap_d,
                rank=int(payload.get("cmc_rank") or 0) or None,
                last_updated=str(usd.get("last_updated") or "") or None,
            )
        if self._cache:
            self._cache.set(cache_key, out, self._cfg.quote_cache_ttl)
        return out

    def listings_top(self, limit: int) -> list[TopCryptoRow]:
        limit = max(1, min(int(limit), 50))
        cache_key = f"cmc:listings:{limit}:{self._cfg.convert_currencies}"
        if self._cache:
            hit = self._cache.get(cache_key)
            if isinstance(hit, list):
                return hit

        converts = _parse_convert_currencies(self._cfg.convert_currencies)
        merged_by_id: dict[int, dict] = {}
        order: list[int] = []
        for conv in converts:
            params = {
                "start": "1",
                "limit": str(limit),
                "convert": conv,
                "sort": "market_cap",
                "sort_dir": "desc",
            }
            raw = self._get_json("/cryptocurrency/listings/latest", params)
            data = raw.get("data") or []
            if not isinstance(data, list):
                continue
            if not order:
                for item in data:
                    if isinstance(item, dict) and item.get("id") is not None:
                        order.append(int(item["id"]))
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("id")
                if cid is None:
                    continue
                cid_i = int(cid)
                if cid_i not in merged_by_id:
                    merged_by_id[cid_i] = dict(item)
                    merged_by_id[cid_i]["quote"] = {}
                q = item.get("quote") or {}
                if conv in q:
                    merged_by_id[cid_i]["quote"][conv] = q[conv]
        rows: list[TopCryptoRow] = []
        seq = order if order else sorted(merged_by_id.keys())
        for cid_i in seq:
            item = merged_by_id.get(cid_i)
            if not item:
                continue
            if not isinstance(item, dict):
                continue
            quote = item.get("quote") or {}
            usd = quote.get("USD") or {}
            ars = quote.get("ARS") or {}
            try:
                price_usd = Decimal(str(usd.get("price") or "0"))
            except Exception:
                price_usd = Decimal("0")
            try:
                price_ars = Decimal(str(ars.get("price") or "0"))
            except Exception:
                price_ars = Decimal("0")
            chg = usd.get("percent_change_24h")
            try:
                chg_d = Decimal(str(chg)) if chg is not None else None
            except Exception:
                chg_d = None
            rank = int(item.get("cmc_rank") or len(rows) + 1)
            rows.append(
                TopCryptoRow(
                    rank=rank,
                    symbol=str(item.get("symbol") or "").upper(),
                    name=str(item.get("name") or ""),
                    price_usd=price_usd,
                    price_ars=price_ars,
                    percent_change_24h=chg_d,
                )
            )
        if self._cache:
            self._cache.set(cache_key, rows, self._cfg.listings_cache_ttl)
        return rows
