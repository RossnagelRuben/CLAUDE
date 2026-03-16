# Transcripción de voz en tiempo real e integración N8N

Para que la voz sea **más rápida**, que **no se cuelgue** al mandar un segundo audio y poder usar **N8N**, se añadió:

1. **API de transcripción** (proceso aparte)
2. **Serialización por usuario** en el bot (un audio a la vez por usuario)
3. **Integración opcional con N8N**

---

## Pasos rápidos (para que no se cuelgue)

1. **Levantá la API de transcripción** (en la misma máquina del bot):
   ```bash
   cd /root/telegram-bot
   ./venv/bin/uvicorn transcription_api:app --host 0.0.0.0 --port 8765
   ```
   Dejalo corriendo (o en segundo plano / systemd).

2. **En el `.env` del bot** agregá:
   ```env
   TRANSCRIBE_API_URL=http://127.0.0.1:8765
   ```

3. **Reiniciá el bot**. A partir de ahí la transcripción la hace la API y el bot no se bloquea; si tarda, verás respuesta en lugar de quedar colgado en "Escuchando y transcribiendo...".

**Si querés usar N8N en el medio** (para logs, pasos extra, etc.): ver más abajo la sección "Opción B: Bot → N8N → API".

---

## 1. API de transcripción (recomendado)

La API corre en un proceso separado. El bot le envía el audio y recibe el texto, sin bloquearse.

### Cómo levantar la API

Desde la raíz del proyecto (donde está `jarvis_bot.py`):

```bash
# Instalar FastAPI y uvicorn si no están
pip install fastapi uvicorn

# Levantar la API (puerto 8765)
uvicorn transcription_api:app --host 0.0.0.0 --port 8765
```

O con el venv del proyecto:

```bash
./venv/bin/pip install fastapi uvicorn
./venv/bin/uvicorn transcription_api:app --host 0.0.0.0 --port 8765
```

### Configurar el bot para usar la API

En el `.env` del bot:

```env
TRANSCRIBE_API_URL=http://localhost:8765
```

Si la API está en otra máquina, usá esa URL (ej. `http://192.168.1.10:8765`).

Con esto:

- La transcripción se hace en la API, no dentro del bot.
- El bot no se bloquea ni se cuelga con un segundo audio.
- Solo se procesa **un audio por usuario a la vez**: si mandás otro mientras responde el primero, el bot contesta: *"Estoy procesando tu audio anterior..."*.

---

## 2. N8N incorporado

Podés usar N8N de dos maneras.

### Opción A: Bot → API directo (más simple y rápido)

- Bot tiene `TRANSCRIBE_API_URL=http://localhost:8765`.
- La API de transcripción está levantada en el 8765.
- N8N no participa en la transcripción; lo podés usar para otras cosas (informes, logs, etc.).

### Opción B: Bot → N8N → API (N8N en el medio)

Así el flujo es: **Bot → N8N (webhook) → API de transcripción → N8N → Bot**. N8N queda en el medio y podés sumar pasos (log, base de datos, notificaciones, etc.).

**Configurar N8N (pasos concretos):**

1. **En N8N:** Workflows → **Import from File** (o pegar JSON). Importá el archivo `n8n/transcripcion-voz.json` del proyecto.
2. **Abrí el nodo "Llamar API transcripción"** y en **URL** poné la dirección de tu API de transcripción:
   - Si N8N corre en **Docker** y la API en el mismo host: `http://172.17.0.1:8765/transcribe` (el JSON ya lo trae así).
   - Si N8N y la API están en el mismo servidor sin Docker: `http://localhost:8765/transcribe`
   - Si la API corre en otro servidor: `http://IP_DEL_SERVIDOR:8765/transcribe`
3. **Activá el workflow** (toggle en ON).
4. **Copiá la URL de producción del Webhook:** en el nodo "Webhook Recibir audio" → **Production URL** (ej. `https://tu-instancia-n8n.com/webhook/transcribir`).
5. **En el `.env` del bot** poné esa URL:
   ```env
   TRANSCRIBE_API_URL=https://tu-instancia-n8n.com/webhook/transcribir
   ```
6. Reiniciá el bot.

El workflow hace: recibe el POST con el audio → llama a tu API `/transcribe` → devuelve `{"text": "..."}` al bot.

---

## 3. Sin API (solo bot)

Si **no** configurás `TRANSCRIBE_API_URL`, el bot sigue transcribiendo **dentro del mismo proceso** con `transcribe_core` (faster_whisper). Sigue habiendo **un audio por usuario a la vez**; si mandás un segundo, el bot responde que está procesando el anterior.

---

## 4. N8N ya corriendo en el servidor (Docker)

En este servidor N8N no se instala con npm (no está en el PATH). Se usa Docker:

- **Arrancar:** `docker start n8n` (si el contenedor ya existe) o  
  `docker run -d --name n8n -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n`
- **Interfaz:** http://IP_DEL_SERVIDOR:5678
- El asistente (Jarvis) tiene en su prompt que proponga siempre estos comandos Docker para N8N, no npm.

---

## 5. Resumen de variables

| Variable | Uso |
|----------|-----|
| `TRANSCRIBE_API_URL` | URL de la API de transcripción (ej. `http://localhost:8765`) o la URL del webhook de N8N si usás Opción B. |

---

## 6. Archivos implicados

- `transcribe_core.py`: lógica Whisper (usada por el bot en proceso y por la API).
- `transcription_api.py`: servicio FastAPI para transcripción.
- `n8n/transcripcion-voz.json`: workflow N8N para poner N8N delante de la API (opcional).

---

## 7. Log de auditoría (registro en tiempo real)

El bot escribe en `agent_data/logs/jarvis_audit.log` eventos de voz para diagnóstico y mejora:

- **voice_start**: inicio de procesamiento (user_id, si usa API).
- **voice_api_ok**: API/N8N respondió bien (duration_sec, bytes).
- **voice_api_fail**: fallo de API (detalle, duration_sec).
- **voice_api_timeout**: API no respondió a tiempo; se usó fallback local.
- **voice_local_fallback**: se transcribió en proceso tras fallo de API.
- **voice_timeout**: se superó el límite total; se responde al usuario y se libera.
- **voice_error**: excepción no recuperable.

Formato por línea: `[YYYY-MM-DD HH:MM:SS] event | detail key=value ...`  
Revisando este archivo podés ver cuántos timeouts o fallbacks hay y ajustar timeouts o la API.
