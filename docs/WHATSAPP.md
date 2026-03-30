# Jarvis en WhatsApp

Integración WhatsApp usando **Evolution API** (self-hosted) + `whatsapp_bridge.py`.

## Arquitectura

```
WhatsApp
  ↕
Evolution API  (Docker, puerto 8080)
  ↕ webhook POST /webhook
whatsapp_bridge.py  (FastAPI, puerto 8766)
  ↕
Claude API  (mismo system prompt que Telegram)
  ↕
server_executor.py  (comandos del servidor)
```

N8N corre independiente para monitoreo proactivo (alertas automáticas por WhatsApp).

---

## 1. Levantar Evolution API

```bash
docker run -d \
  --name evolution-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -e SERVER_URL=http://TU_IP_PUBLICA:8080 \
  -e AUTHENTICATION_API_KEY=jarvis-secret \
  -e WEBHOOK_GLOBAL_URL=http://172.17.0.1:8766/webhook \
  -e WEBHOOK_GLOBAL_ENABLED=true \
  -e WEBHOOK_EVENTS_MESSAGES_UPSERT=true \
  -v evolution_store:/evolution/store \
  atendai/evolution-api:latest
```

> `172.17.0.1` es la IP del host desde dentro de Docker. Si el bridge corre en el host, esto funciona directamente.

---

## 2. Crear instancia y escanear QR

```bash
# Crear instancia llamada "jarvis"
curl -X POST http://localhost:8080/instance/create \
  -H "apikey: jarvis-secret" \
  -H "Content-Type: application/json" \
  -d '{"instanceName": "jarvis", "qrcode": true}'

# Ver QR en el navegador
curl http://localhost:8080/instance/qrcode/jarvis \
  -H "apikey: jarvis-secret"
```

Abrir WhatsApp → Dispositivos vinculados → Vincular dispositivo → escanear QR.

---

## 3. Configurar `.env`

```env
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=jarvis-secret
EVOLUTION_INSTANCE=jarvis
WHATSAPP_ALLOWED_NUMBERS=549XXXXXXXXXX   # tu número, sin + ni espacios
WHATSAPP_SESSION_HOURS=8
AGENT_SECRET=tu_clave   # obligatoria: misma que usás para iniciar sesión por WhatsApp; el panel /admin/whatsapp la pide para logout/QR
# ADMIN_PANEL_TOKEN=...  # opcional; si no va, por defecto = AGENT_SECRET
```

Hay una plantilla en `.env.example` del repo.

---

## 4. Iniciar el bridge

```bash
./venv/bin/uvicorn whatsapp_bridge:app --host 0.0.0.0 --port 8766
```

Verificar estado: `curl http://localhost:8766/status`

### 4.1 Systemd (recomendado en VPS)

Para que siempre corra el **código actual** (rutas `/walogout`, panel, etc.) y se reinicie solo:

```bash
cd /ruta/al/telegram-bot
cp -n .env.example .env   # si aún no tenés .env; editá AGENT_SECRET y Evolution
python3 -m venv venv
./venv/bin/pip install -r requirements-bridge.txt
chmod +x scripts/install_jarvis_whatsapp_bridge_service.sh scripts/deploy_bridge.sh scripts/verify_bridge_health.sh
sudo ./scripts/deploy_bridge.sh
```

Equivalente manual: solo `sudo ./scripts/install_jarvis_whatsapp_bridge_service.sh`.

El unit está en `systemd/jarvis-whatsapp-bridge.service` (ajustá rutas si no usás `/root/telegram-bot`).

Tras el arranque, en el log debe aparecer `whatsapp_bridge iniciado: ... walogout=True evolution-qr=True`. Si `walogout=False`, el proceso **no** es el binario nuevo.

### 4.2 Nginx delante del puerto 8766

Si **nginx** solo reenvía parte de las URLs, `/walogout` puede devolver 404. Hay que **proxy_pass** de todo el sitio al bridge, no solo `/admin/whatsapp`. Ver ejemplo en `deploy/nginx-jarvis-bridge.example.conf`.

---

## 5. Uso desde WhatsApp

1. Enviá tu `AGENT_SECRET` al número vinculado → Jarvis responde "Sesión iniciada".
2. A partir de ahí, chateá normalmente igual que con Telegram.

**Comandos que entiende Jarvis:**
- Texto libre → responde con la IA (Gemini/Claude según `.env`)
- Si sugiere un comando del servidor → te pregunta confirmación → respondé `SI` para ejecutar
- Búsquedas web → cuando el modelo usa `BUSCAR:`
- Imágenes / edición / audio → `IMAGEN:`, edición de última imagen, `AUDIO:` (ver `agent_prompt.txt`)
- Productos DRR → `PRODUCTOS:`
- Google Calendar / Drive → OAuth configurado; `CALENDAR_PROPUESTA:`, `DRIVE_SUBIR:`
- Notas → `NOTA:` / listado según prompt

**Menú:** `menú`, `/menu`, `/start` y **«qué podés hacer»** usan el mismo `WHATSAPP_MENU_BUTTONS_SPEC`: varios mensajes de hasta **3 botones** cada uno (límite de WhatsApp). El texto del primer mensaje puede personalizarse con `WHATSAPP_HELP_*` cuando la frase es de ayuda.

El bridge envía primero `sendButtons` con el JSON plano que espera **Evolution API** (`type: reply` en cada botón). Si tu instancia usa Baileys y los botones no se ven como reply nativos, Evolution puede mostrar otro formato; como respaldo: `WHATSAPP_MENU_LIST_FALLBACK=1` envía **lista** con **todas** las opciones (hasta 10 filas), no solo tres.

Ítems: `WHATSAPP_MENU_BUTTONS_SPEC` en `.env` (ver `.env.example`).

**La sesión expira** después de `WHATSAPP_SESSION_HOURS` horas. Volvé a mandar la clave para reactivar.

---

## 6. Monitoreo proactivo con N8N

Importar `n8n/whatsapp-monitoreo.json` en N8N:

1. Ir a N8N → Workflows → Import from file
2. Importar `n8n/whatsapp-monitoreo.json`
3. En el nodo "Enviar alerta por WhatsApp":
   - Cambiar la URL a `http://172.17.0.1:8080/message/sendText/jarvis`
   - En el body, reemplazar `WHATSAPP_ALLOWED_NUMBER` con tu número
   - Agregar header `apikey: jarvis-secret`
4. Activar el workflow

El workflow revisa disco, RAM y load cada 15 minutos. Si alguno supera el 85%, te manda un WhatsApp automático.

---

## Solución de problemas

| Problema | Causa probable | Solución |
|---|---|---|
| Evolution API no recibe QR | `SERVER_URL` incorrecto | Usar IP pública o `localhost` según acceso |
| Bridge no recibe webhooks | `WEBHOOK_GLOBAL_URL` apunta mal | Verificar que `172.17.0.1:8766` sea accesible desde el contenedor |
| Jarvis no responde | Número no en `WHATSAPP_ALLOWED_NUMBERS` | Agregar número al `.env` y reiniciar bridge |
| "Evolution API no configurada" | `EVOLUTION_API_URL` vacío en `.env` | Completar variable y reiniciar bridge |
| Logout / QR del panel → 404 | Código viejo o nginx no enruta al bridge | `install_jarvis_whatsapp_bridge_service.sh` + nginx `proxy_pass` completo; **Plan B:** `GET /status?evolution_logout=1&token=AGENT_SECRET` (también botón en el panel) |
| Cerrar sesión sin panel | Evolution directo | `curl -X DELETE "http://localhost:8080/instance/logout/INSTANCIA" -H "apikey: KEY"` |
