# Filtros DRR en WhatsApp (Jarvis)

Este documento describe cómo `whatsapp_bridge.py` interpreta filtros de **productos DRR** cuando el pedido llega vía **audio (transcripción)** o texto.

## Qué filtros se soportan

1. **Cantidad de productos**
   - Ejemplos: `traeme 5 productos`, `5 productos con ...`
   - Si el usuario no dice un número, se usa la cantidad que proponga el modelo en `PRODUCTOS: descripcion | cantidad`.

2. **Mostrar precios / no mostrar precios**
   - Mostrar: `con precios`, `con precio`, `precio` (sin negar explícitamente)
   - No mostrar: `sin precios`, `sin precio`

3. **Orden por “última modificación”**
   - Si el texto menciona `última/ultima modificación` (o `actualización`) o `recientes`, se intenta ordenar localmente por campos de fecha que vengan en los datos extra del producto.
   - Si la API no trae ninguna fecha reconocible, el orden queda como lo entregue DRR.

## Registro (logs)

- El bridge imprime en el log `whatsapp_stdout.log` la línea:
  `DRR filtros (from user audio/text): ...`
- Esto permite verificar rápidamente qué filtro se aplicó y por qué.

