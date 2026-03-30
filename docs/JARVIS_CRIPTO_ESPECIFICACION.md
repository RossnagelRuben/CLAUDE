# Jarvis Cripto — especificación (Fase 1 y extensión 1.5)

Documento de referencia alineado al pedido original. La implementación vive en el paquete `crypto/` y los comandos expuestos son `/cripto …` (Telegram y WhatsApp).

## Implementado en código

| Requisito | Ubicación / notas |
|-----------|-------------------|
| Consultar precios, SOL/USDT, USD+ARS, top | `CoinMarketCapProvider`, `formatter`, comandos `precio`, `top` |
| Historial de consultas | `JsonQueryHistoryStore` en `agent_data/crypto/history/` |
| Balance simulado | `JsonSimulatedPortfolioStore` en `agent_data/crypto/portfolio/` |
| Telegram + WhatsApp sin duplicar lógica | `try_handle_crypto_command` + `jarvis_bot.py` + `whatsapp_bridge.py` |
| Jupiter quote + build + sin ejecución automática | `JupiterTradingProvider`, `ITradingProvider` |
| Sin claves privadas en servidor | `execute_transaction` solo confirma intención; firma en wallet |

## Estructura del módulo

```text
crypto/
  __init__.py          # build_crypto_service()
  interfaces.py        # IMarketDataProvider, IQueryHistoryStore, ISimulatedPortfolioStore, ITradingProvider
  models.py
  service.py           # CryptoService
  formatter.py
  providers/
    coinmarketcap_provider.py
    jupiter_provider.py
  storage.py
  cache.py
  commands.py
  config.py
```

## Variables de entorno

Ver `.env.example`: `COINMARKETCAP_API_KEY` (o `CMC_API_KEY`), `CRYPTO_CMC_CONVERT`, `SOLANA_RPC_URL`, URLs Jupiter opcionales.

## Texto original del pedido (resumen fiel)

- Trabajar sobre el repositorio existente; no app paralela.
- Referencia de estilo SOLID como `drr/` (protocolos + inyección).
- Priorizar Gemini en el resto del bot; datos de mercado con CoinMarketCap; nueva funcionalidad cripto sin depender de Claude.
- Fase 1: precios, SOL y USDT, USD y ARS, top, historial, balance simulado, Telegram y WhatsApp, base para fases futuras.
- Excluido: compra/venta real, firmas automáticas, trading autónomo, señales IA agresivas, órdenes invisibles, custodia de claves.
- Fase 1.5: operaciones preparadas, cotización swap, construcción de transacción, confirmación manual, registro; Jupiter como proveedor Solana desacoplado; flujo: solicitud → validación → cotización → armado → confirmación explícita → ejecución/cancelación (en servidor: sin broadcast; ejecución real en wallet del usuario).

---

*El prompt largo del usuario terminaba citando `crypto/providers/jupiter_provider.py`; ese archivo está implementado en el repo.*
