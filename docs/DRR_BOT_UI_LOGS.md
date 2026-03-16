# DRR Bot: UI de listado, botones y logging

Documentación de la interfaz de productos DRR en Telegram y del log dedicado para revisar y depurar fallos.

---

## 1. Formato del listado de productos

El listado se genera en `drr/formatter.py` (principio **SRP**: un único lugar para el formato).

- **Una línea por producto:** `N. 🖼|⬜ Descripción (50 chars) · Cód: XXX` (Cód solo si existe).
  - `🖼` = producto con imagen asignada en API.
  - `⬜` = producto sin imagen.
- No se muestran corchetes vacíos `[]` cuando no hay código; la lectura es más clara.

Ejemplo:

```
1. ⬜ COMBO PRIMAVERA COCA COLA X 350CC · Cód: 7891234567890
2. 🖼 GASEOSA COCA COLA X 600ML · Cód: 7891234567891
3. ⬜ PRODUCTO SIN CÓDIGO
```

---

## 2. Botones inline

### 2.1 Tras listar productos (🖼 1, 🖼 2, …)

- Cada producto de la lista tiene un botón **🖼 N** (N = 1, 2, …).
- **Acción:** al pulsar, se ejecuta una búsqueda de imagen para ese producto (código + descripción) y se muestran hasta 5 imágenes con navegación.
- El bot debe recibir updates de tipo `callback_query` (en `run_polling`: `allowed_updates=["message", "callback_query"]`).

### 2.2 Navegación entre imágenes (◀ Anterior / Siguiente ▶)

- Tras una búsqueda de imagen (por comando o por botón 🖼 N), se muestran **◀ Anterior** y **Siguiente ▶**.
- El estado (URLs e índice actual) se guarda en `context.user_data` (`drr_image_search_urls`, `drr_image_search_index`).
- Al pulsar, se edita el mensaje con la nueva imagen y el pie actualizado (p. ej. `2/5 — Para guardar: ...`).

---

## 3. Log dedicado DRR (`logs/drr_bot.log`)

Para revisar y arreglar fallos sin mezclar con el log general del bot.

- **Ubicación:** `{BASE_DIR}/logs/drr_bot.log` (mismo `logs/` que el resto del bot).
- **Formato por línea:** `YYYY-MM-DD HH:MM:SS [LEVEL] action — detail`.

### Acciones registradas

| Acción               | Cuándo                         | Detalle (resumen)                          |
|----------------------|--------------------------------|--------------------------------------------|
| `listar`             | Tras listar con filtros        | filtros y cantidad de productos            |
| `listar_atajo`       | Tras atajo `/productos coca`   | query y cantidad                           |
| `imagen_buscar`      | Tras buscar imagen por texto  | query y número de URLs                      |
| `callback_buscar`    | Al pulsar 🖼 N                 | índice, id, query; si hay error, nivel ERROR |
| `callback_img_nav`   | Al pulsar ◀ / ▶               | dirección y nuevo índice; errores con nivel ERROR |

### Cómo usarlo

```bash
# Ver últimas líneas
tail -f telegram-bot/logs/drr_bot.log

# Buscar errores
grep ERROR telegram-bot/logs/drr_bot.log

# Ver solo callbacks de botones
grep callback telegram-bot/logs/drr_bot.log
```

Si un botón “no hace nada”, revisar en este log si aparece `callback_buscar` o `callback_img_nav` y si hay alguna línea `ERROR` o `WARNING` después.

---

## 4. Principios SOLID aplicados

- **SRP:** `formatter.py` solo formatea; `drr/logger.py` solo escribe el log DRR; handlers en el bot orquestan.
- **OCP:** Nuevas acciones de log se añaden con nuevos llamados a `drr_log()` sin cambiar la firma del logger.
- **DIP:** El bot depende de `drr.logger.drr_log` (abstracción de “escribir log DRR”), no de detalles de archivo.

---

## 5. ¿Conviene incluir N8N en este proyecto?

**Resumen:** solo si querés automatizar flujos **fuera** del bot (reportes, sincronización con otras apps, webhooks, cron).

- **Qué es N8N:** herramienta de automatización de flujos (workflows) con nodos (APIs, cron, webhooks, etc.).
- **Ventajas si lo sumás:**
  - Programar tareas (p. ej. “cada noche listar productos sin imagen y enviar resumen por e-mail/Slack”).
  - Conectar la API DRR con Google Sheets, bases de datos o otros servicios sin tocar código del bot.
  - Recibir webhooks de otros sistemas y reaccionar (p. ej. “cuando se crea un producto, buscar imagen y notificar por Telegram”).
- **Cuándo no hace falta:**
  - Si todo lo que necesitás es usar el bot en Telegram (listar, buscar imágenes, guardar): el bot ya lo cubre.
- **Recomendación:** mantener el bot como está. Añadir **N8N** solo si más adelante necesitás automatizaciones programadas o integraciones con otras herramientas; en ese caso N8N puede llamar a la misma API DRR y/o enviar mensajes al bot (por API de Telegram o por un endpoint propio que vos expongas).
