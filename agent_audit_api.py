"""
API simple de auditoría para agentes/workflows (pensada para n8n).

- Escribe eventos en JSONL (una línea por evento) por día.
- Guarda en NEXTCLOUD_DIR si existe; si no, en ./logs.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = Path(os.getenv("NEXTCLOUD_DIR", "").strip() or str(BASE_DIR / "logs"))
AUDIT_DIR = LOG_DIR / "agent_audits"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Audit API", version="1.0")


class AuditEvent(BaseModel):
    agent: str = Field(default="unknown", description="Nombre del agente/workflow")
    event: str = Field(default="event", description="Tipo de evento (run_ok, run_error, etc.)")
    payload: Any = Field(default_factory=dict, description="Datos del evento (dict o JSON string)")


def _audit_file_for_day(day: str) -> Path:
    return AUDIT_DIR / f"agent_audit_{day}.jsonl"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/audit")
async def audit(evt: AuditEvent):
    try:
        now = datetime.now()
        day = now.strftime("%Y-%m-%d")
        payload = evt.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        record = {
            "ts": now.isoformat(timespec="seconds"),
            "agent": evt.agent,
            "event": evt.event,
            "payload": payload,
        }
        line = json.dumps(record, ensure_ascii=False)
        _audit_file_for_day(day).open("a", encoding="utf-8").write(line + "\n")
        logger.info("audit ok agent=%s event=%s", evt.agent, evt.event)
        return {"ok": True}
    except Exception as e:
        logger.exception("audit fail: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])

