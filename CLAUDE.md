# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Project

```bash
# Start the main Telegram bot
./venv/bin/python jarvis_bot.py

# Start the voice transcription API (separate process, port 8765)
./venv/bin/uvicorn transcription_api:app --host 0.0.0.0 --port 8765
```

No build step or test suite is configured. There is no Makefile, `pytest`, or `unittest` setup.

**N8N must be run via Docker only** — there is no Node.js/npm in PATH:
```bash
docker run ... n8nio/n8n
docker start <container>
```

## Configuration

All secrets and URLs come from a `.env` file (not committed). Key variables:

| Variable | Purpose |
|---|---|
| `TELEGRAM_TOKEN` | Bot token |
| `CLAUDE_API_KEY` | Anthropic Claude API |
| `ALLOWED_CHAT_ID` | Authorized Telegram user |
| `AGENT_SECRET` | Bot auth passphrase |
| `GEMINI_API_KEY` | Google Gemini (images, Live voice) |
| `OPENAI_API_KEY` | OpenAI (image generation fallback) |
| `DRR_API_BASE_URL` + `DRR_CLIENT_TOKEN` | DRR product API |
| `DRR_DEV_USER` / `DRR_DEV_PASSWORD` | DRR token flow credentials |
| `TRANSCRIBE_API_URL` | External transcription service URL |
| `USE_OPENCLAW` | Set to enable OpenClaw AI backend |
| `OPENCLAW_GATEWAY_WS_URL` | WebSocket gateway for OpenClaw |
| `NEXTCLOUD_DIR` | Optional local Nextcloud sync path |

## Architecture

### Main Bot (`jarvis_bot.py`)
The single 2100+ line entry point. Handles all Telegram command/message routing, session auth, daily Markdown log management, and delegates to specialized modules. AI backend is selected at runtime: Claude (default), OpenClaw, or Gemini Live.

- `ask_claude()` — synchronous Claude call with conversation context
- `get_ai_response()` — async wrapper supporting Claude or OpenClaw
- `_transcribe_via_api()` / `_transcribe_local()` — voice transcription with fallback
- `_gemini_live_voice_response()` — native audio via Gemini Live (requires ffmpeg for OGG→PCM)
- `append_log()` — writes to daily `logs/YYYY-MM-DD.md`

Claude is called with `claude-haiku-4-5-20251001` at max 800 tokens. The system prompt is loaded from `agent_prompt.txt` (Spanish).

### DRR Product Module (`drr/`)
Well-layered SOLID architecture for product catalog API integration:

- **`models.py`** — `Producto` dataclass (domain model)
- **`interfaces.py`** — Protocol-based contracts: `IProductoRepository`, `IBuscadorImagenes`, `IAlmacenImagenes`
- **`service.py`** — `ServicioProductos` orchestrates business logic via injected dependencies
- **`api_client.py`** — HTTP client with TTL cache (default 25s, max 300 items) and bearer token auth
- **`auth.py`** — 3-stage token flow: `CLIENT_TOKEN → TOKEN_DEV → TOKEN_USER → final Bearer`
- **`cache.py`** — Generic `TTLCache` with FIFO eviction
- **`storage.py`** — `AlmacenImagenesLocal` saves product images to `productos_imagenes/`
- **`image_search.py`** — `DuckDuckGoBuscadorImagenes` fetches fallback images
- **`formatter.py`** — Formats product data as Telegram messages
- **`logger.py`** — Dedicated `drr_bot.log` via `drr_log()`

### Voice Transcription
Two paths exist:
1. **External API** (preferred): POST multipart to `TRANSCRIBE_API_URL`
2. **Local fallback**: `transcribe_core.py` wraps `faster_whisper` (base model, CPU, int8)

`transcription_api.py` is a standalone FastAPI service (port 8765) used by N8N workflows.

### Logging & Audit
- **Daily logs**: `logs/YYYY-MM-DD.md` — conversation history in Markdown
- **Audit log**: `agent_data/logs/jarvis_audit.log` — JSON-structured real-time audit trail
- **DRR log**: `drr_bot.log` — DRR module operations

## Code Conventions

Follow **SOLID principles** throughout (enforced by `.cursor/rules/solid-comments-docs.mdc`):
- New integrations should use Protocol-based interfaces (see `drr/interfaces.py` as the pattern)
- Add docstrings explaining non-obvious logic; avoid redundant comments on self-explanatory code
- Document new commands, integrations, or significant features in `docs/` as Markdown files
