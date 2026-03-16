# Integración OpenClaw en Jarvis

Jarvis puede usar **OpenClaw** como backend de IA en lugar de (o además de) Claude. OpenClaw es un framework de agentes autónomos con soporte multi-modelo y múltiples canales.

## Requisitos

- Instancia de OpenClaw corriendo (gateway WebSocket o API compatible con OpenAI).
- SDK de Python: `pip install -r requirements-openclaw.txt`

## Cómo hacer que funcione con OpenClaw

1. **Instalá el SDK** (una vez):
   ```bash
   pip install -r requirements-openclaw.txt
   ```

2. **Tené el gateway de OpenClaw corriendo** (ej. en la misma máquina):
   ```bash
   openclaw gateway
   ```
   Por defecto escucha en `ws://127.0.0.1:18789/gateway`.

3. **En tu `.env`** activá OpenClaw con una de estas opciones:

   | Variable | Descripción |
   |----------|-------------|
   | `USE_OPENCLAW=1` | Activa OpenClaw con gateway local (`ws://127.0.0.1:18789/gateway`). La opción más simple. |
   | `OPENCLAW_GATEWAY_WS_URL` | URL del gateway WebSocket (ej: `ws://127.0.0.1:18789/gateway` o URL remota) |
   | `OPENCLAW_OPENAI_BASE_URL` | URL base de la API compatible con OpenAI (alternativa al WS) |
   | `OPENCLAW_AGENT_ID` | ID del agente en OpenClaw (por defecto: `jarvis`) |

   Con **solo** `USE_OPENCLAW=1` ya usa el gateway local. Si OpenClaw falla (timeout, no conecta), se hace fallback a Claude si tenés `CLAUDE_API_KEY` configurado.

## Modos de uso

1. **Solo Claude** (por defecto): no definas variables OpenClaw. Se usa `CLAUDE_API_KEY`.
2. **Solo OpenClaw**: definí `USE_OPENCLAW=1` (o `OPENCLAW_GATEWAY_WS_URL`). No hace falta `CLAUDE_API_KEY`.
3. **OpenClaw con fallback a Claude**: definí ambos; ante error de OpenClaw se usa Claude.

## Arranque de OpenClaw

Necesitás tener el gateway de OpenClaw en marcha, por ejemplo:

```bash
# Con el CLI de OpenClaw (si lo tenés instalado)
openclaw gateway
```

El SDK se conecta por defecto a `ws://127.0.0.1:18789/gateway` si no indicás otra URL. Ver [documentación de OpenClaw](https://docs.openclaw.ai/) para instalar y configurar el gateway y los agentes.

---

## Resumen: que funcione con OpenClaw

1. SDK ya está en el proyecto (`pip install -r requirements-openclaw.txt`).
2. En tu `.env` agregá: **`USE_OPENCLAW=1`**
3. Arrancá el gateway de OpenClaw (en otra terminal o servicio): `openclaw gateway`
4. Reiniciá el bot. Al iniciar deberías ver en log: `Jarvis bot iniciado (IA: OpenClaw)...`
