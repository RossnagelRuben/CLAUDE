# Puertos y URLs — Jarvis (servidor / bridge / servicios)

Referencia para saber **qué escucha en cada puerto** y **para qué sirve cada ruta HTTP** del proyecto. Sustituí `TU_IP` por la IP o dominio público del VPS (ej. `104.225.140.9`).

---

## 1. Resumen de puertos

| Puerto | Servicio típico | Descripción breve |
|--------|-----------------|-------------------|
| **8766** | `whatsapp_bridge.py` (uvicorn) | **Bridge principal Jarvis**: webhook WhatsApp, paneles admin, QR Evolution embebido, API interna. |
| **8080** | Evolution API (Docker) | API de WhatsApp (instancias, QR, envío de mensajes). Mapeo habitual `-p 8080:8080`. |
| **5678** | N8N (Docker) | Automatizaciones, webhooks de transcripción, monitoreos. UI: `http://TU_IP:5678`. |
| **8765** | `transcription_api.py` (uvicorn) | API de transcripción de voz (opcional; si usás N8N como proxy, puede no exponerse). |
| **8099** | `qr_server.py` (HTTP simple) | **Opcional y legacy**: página de QR/logout aparte. Hoy el QR del Evolution suele ir **dentro del bridge** (`8766/evolution/`). Puerto por `QR_SERVER_PORT` (default 8099). |
| **17860** | `sd_a1111_bridge` (público) | **Opcional**: puente estilo API A1111 + imágenes públicas. Variable `SD_BRIDGE_PUBLIC_PORT` (default 17860). |
| **7860** | `sd_a1111_bridge` (interno) | Misma app, escucha en `172.17.0.1:7860` para que Docker/N8N hable con el host sin exponer a internet. |
| **18789** | OpenClaw gateway (si lo usás) | WebSocket del gateway local; Telegram/Jarvis puede usar `ws://127.0.0.1:18789/gateway` con `USE_OPENCLAW=1`. |

**Firewall / nginx:** abrís al público lo que necesites (suele ser **80/443** con nginx → **8766**, y **8080** solo si administrás Evolution desde fuera; **5678** solo si querés la UI de N8N expuesta).

---

## 2. Bridge Jarvis — `http://TU_IP:8766`

Proceso: `uvicorn whatsapp_bridge:app --host 0.0.0.0 --port 8766`.

Evolution debe enviar webhooks a:

```text
http://TU_IP:8766/webhook
```

(o la IP que vea el contenedor Docker, ej. `http://172.17.0.1:8766/webhook`).

### 2.1 Salud y diagnóstico

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/status` | Estado del bridge (claves configuradas, sesiones, flags de features). Permite logout Evolution por query en algunos despliegues (`evolution_logout=1&token=...`). |
| GET | `/openapi.json` | Esquema OpenAPI (FastAPI); útil para ver si el despliegue incluye rutas nuevas (`walogout`, etc.). |

### 2.2 Webhook y chat interno

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| POST | `/webhook` | Entrada principal de **Evolution API** (`messages.upsert`). Procesa texto, audio, medios. |
| POST | `/chat` | Integración N8N u otros: body JSON `message`, `phone`, `jid` para disparar el mismo flujo que un mensaje entrante. |

### 2.3 Evolution — QR y logout (rutas cortas)

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/evolution/` | Página HTML con QR y logout (Evolution). |
| GET | `/evolution/api/qr` | JSON/API del QR para el panel. |
| POST / GET | `/evolution/api/logout` | Cerrar sesión de la instancia en Evolution. |

Alias bajo `/admin/whatsapp/evolution/...` (mismo comportamiento) por si nginx solo proxea `/admin/whatsapp`.

Atajos de logout (mismo cuerpo/query que el resto de logout Evolution):

- `GET`/`POST` `/walogout`
- `GET`/`POST` `/evo-logout`
- `GET`/`POST` `/admin/evo-logout`
- `GET`/`POST` `/admin/whatsapp/evo-logout`

### 2.4 Rutas cortas `/j/*` (útiles si un proxy bloquea rutas largas)

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/j/ping` | Comprueba que el proceso es el bridge correcto. |
| GET / POST | `/j/logout` | Logout Evolution (token + instancia opcional). |
| POST | `/j/instances` | Lista/diagnóstico de instancias Evolution (según implementación). |
| GET / POST | `/j/debug-events` | Eventos de depuración recientes (si está habilitado el log de debug). |

### 2.5 Panel admin WhatsApp — números y Evolution

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/admin/whatsapp` | **Panel HTML** (números permitidos, mapa de teléfonos, QR, logout, diagnóstico). |
| POST | `/admin/whatsapp` | Fallback JSON: `wa_op` en el cuerpo cuando `GET` con query no es posible. |
| GET / POST | `/admin/whatsapp/api/evolution-qr` | API QR (variantes según versión). |
| POST | `/admin/whatsapp/api/evolution-instances` | Instancias Evolution (panel). |
| POST | `/admin/whatsapp/api/debug-events` | Envío de eventos de debug al panel. |
| POST | `/admin/whatsapp/api/evolution-logout` (y alias `/evolution_logout`, `/evolution/logout`) | Logout desde el panel. |
| GET | `/admin/whatsapp/api/evolution-logout` | Variante GET. |
| GET | `/admin/whatsapp/api/config` | Lee `WHATSAPP_ALLOWED_NUMBERS` y `WHATSAPP_PHONE_MAP` (requiere token admin). |
| POST | `/admin/whatsapp/api/config` | Actualiza esos valores en `.env` (requiere token). |
| GET | `/admin/whatsapp/api/logs` | Últimas líneas filtradas del log del bridge (útil en el panel servidor). |

**Autenticación de paneles y APIs:** header `Authorization: Bearer <ADMIN_PANEL_TOKEN>` o `X-Admin-Token`. Si no definís `ADMIN_PANEL_TOKEN` en `.env`, suele usarse el mismo valor que `AGENT_SECRET`. Algunas rutas de medios admiten `?token=` en la URL.

### 2.6 Bandeja — notas y archivos desde WhatsApp

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/admin/inbox` | Panel HTML: listar/previsualizar notas y archivos en `notes/` y `wa_inbox/media/`. |
| GET | `/admin/inbox/api/items` | JSON listado de archivos. |
| GET | `/admin/inbox/api/raw?rel=...` | Descarga/visualización de un archivo (también `&token=`). |
| POST | `/admin/inbox/api/delete` | Borrar archivos (body `{"rels": ["notes/...", "wa_inbox/..."]}`). |
| PUT | `/admin/inbox/api/note` | Editar contenido de una nota `.txt` bajo `notes/`. |

### 2.7 Panel servidor — métricas

| Método | Ruta | Para qué sirve |
|--------|------|----------------|
| GET | `/admin/server` | Panel HTML (CPU, RAM, disco, gráfico histórico, logs, ajustes de refresco). |
| GET | `/admin/server/api/snapshot?range=24h` | JSON: instantánea + historial. Valores de `range`: `24h`, `72h`, `7d`. |

---

## 3. Evolution API — `http://TU_IP:8080` (ejemplo)

No es el mismo proceso que el bridge; se documenta porque **todo el flujo WhatsApp pasa por aquí** antes del webhook.

- Creación de instancias, QR, envío de mensajes, etc.: ver documentación de Evolution y `docs/WHATSAPP.md`.
- El bridge usa variables `EVOLUTION_API_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` en `.env`.

---

## 4. N8N — `http://TU_IP:5678`

- Interfaz web de workflows.
- Webhooks típicos (ej. transcripción): los define cada workflow importado (`n8n/*.json`).
- Ver `docs/TRANSCRIPCION_Y_N8N.md` y `docs/CONEXION_JARVIS_N8N.md`.

---

## 5. Transcripción — puerto **8765** (opcional)

- Servicio: `uvicorn transcription_api:app --port 8765`.
- Variable en el bridge: `TRANSCRIBE_API_URL` puede apuntar a N8N o directo a esta API.

---

## 6. QR server opcional — puerto **8099**

- Script: `python qr_server.py` (o variable `QR_SERVER_PORT`).
- Solo necesario si **no** usás el QR embebido en `http://TU_IP:8766/evolution/`.

---

## 7. Stable Diffusion / puente A1111 — **17860** y **7860** (opcional)

- Servicio systemd: `sd_bridge_runner.py` levanta dos uvicorn:
  - Público en `0.0.0.0:SD_BRIDGE_PUBLIC_PORT` (default **17860**).
  - Interno en `172.17.0.1:7860` para contenedores.
- Ver `docs/JARVIS_N8N_STABLE_DIFFUSION.md` y `systemd/sd-a1111-bridge.service`.

---

## 8. Nginx delante del bridge

Si usás nginx en **80/443**, conviene **proxy_pass de todo el sitio** a `127.0.0.1:8766`, no solo `/admin/whatsapp`, para que `/walogout`, `/evolution/`, etc. no devuelvan 404. Ejemplo: `deploy/nginx-jarvis-bridge.example.conf`.

---

## 9. Variables útiles en `.env`

| Variable | Relación |
|----------|----------|
| `EVOLUTION_API_URL` | URL base de Evolution (ej. `http://127.0.0.1:8080`). |
| `WEBHOOK_GLOBAL_URL` (Evolution) | Debe apuntar al bridge: `http://…:8766/webhook`. |
| `AGENT_SECRET` | Clave de sesión en WhatsApp; también usada en varios logout del panel. |
| `ADMIN_PANEL_TOKEN` | Token Bearer de los paneles `/admin/*` (si no va, suele igualarse a `AGENT_SECRET`). |
| `QR_WEB_PUBLIC_URL` | Si querés forzar otra base URL para el QR (opcional). |
| `TRANSCRIBE_API_URL` | URL de la API o webhook N8N para transcribir voz. |

Plantilla: `.env.example` en la raíz del repo.

---

*Documento generado para el repo Jarvis / telegram-bot. Actualizá las rutas si añadís nuevos endpoints en `whatsapp_bridge.py`.*
