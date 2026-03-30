# Persistencia local: historial de consultas y portfolio simulado.

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from crypto.interfaces import IQueryHistoryStore, ISimulatedPortfolioStore
from crypto.models import QueryHistoryEntry

logger = logging.getLogger(__name__)


def _decimal_default(obj: object) -> object:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError


class JsonQueryHistoryStore(IQueryHistoryStore):
    """Historial append-only JSONL por usuario."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        safe = "".join(c for c in user_id if c.isalnum() or c in "-_@.")[:120] or "unknown"
        return self.base_dir / f"history_{safe}.jsonl"

    def append(self, entry: QueryHistoryEntry) -> None:
        path = self._path(entry.user_id)
        line = json.dumps(
            {
                "ts_iso": entry.ts_iso,
                "user_id": entry.user_id,
                "action": entry.action,
                "detail": entry.detail,
                "meta": entry.meta,
            },
            ensure_ascii=False,
            default=_decimal_default,
        )
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.warning("No se pudo escribir historial cripto: %s", e)

    def list_recent(self, user_id: str, limit: int) -> list[QueryHistoryEntry]:
        path = self._path(user_id)
        if not path.exists():
            return []
        lines: list[str] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[QueryHistoryEntry] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(
                    QueryHistoryEntry(
                        ts_iso=str(d.get("ts_iso", "")),
                        user_id=str(d.get("user_id", user_id)),
                        action=str(d.get("action", "")),
                        detail=str(d.get("detail", "")),
                        meta=dict(d.get("meta") or {}),
                    )
                )
            except (json.JSONDecodeError, TypeError):
                continue
        return out


class JsonSimulatedPortfolioStore(ISimulatedPortfolioStore):
    """Balance simulado por usuario (JSON)."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        safe = "".join(c for c in user_id if c.isalnum() or c in "-_@.")[:120] or "unknown"
        return self.base_dir / f"portfolio_{safe}.json"

    def _load(self, user_id: str) -> dict[str, str]:
        p = self._path(user_id)
        if not p.exists():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k).upper(): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError, TypeError):
            pass
        return {}

    def _save(self, user_id: str, holdings: dict[str, str]) -> None:
        p = self._path(user_id)
        try:
            p.write_text(json.dumps(holdings, indent=0, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.warning("No se pudo guardar portfolio simulado: %s", e)

    def get_holdings(self, user_id: str) -> dict[str, Decimal]:
        raw = self._load(user_id)
        out: dict[str, Decimal] = {}
        for sym, amt in raw.items():
            try:
                out[sym.upper()] = Decimal(amt)
            except Exception:
                continue
        return out

    def set_holding(self, user_id: str, symbol: str, amount: Decimal) -> None:
        h = self._load(user_id)
        sym = symbol.upper()
        if amount <= 0:
            h.pop(sym, None)
        else:
            h[sym] = format(amount.normalize(), "f")
        self._save(user_id, h)

    def add_holding(self, user_id: str, symbol: str, delta: Decimal) -> None:
        cur = self.get_holdings(user_id).get(symbol.upper(), Decimal("0"))
        self.set_holding(user_id, symbol, cur + delta)

    def reset(self, user_id: str) -> None:
        p = self._path(user_id)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
