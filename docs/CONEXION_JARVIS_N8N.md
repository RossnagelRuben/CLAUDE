# Conectar Jarvis con N8N (transcripción de voz)

Flujo: **Telegram (voz) → Jarvis → N8N (webhook) → API transcripción (8765) → N8N → Jarvis → respuesta**.

## 1. Levantar la API de transcripción (en el servidor)

En una terminal, dejá corriendo:

```bash
cd /root/telegram-bot
./venv/bin/uvicorn transcription_api:app --host 0.0.0.0 --port 8765
```

Si no está levantada, los audios que pasen por N8N fallarán (el bot hará fallback a transcripción local si puede).

## 2. Importar y activar el workflow en N8N

1. Entrá a **http://104.225.140.9:5678**
2. Menú **Workflows** → **Import from File** (o el botón de importar).
3. Elegí el archivo **`n8n/transcripcion-voz.json`** (desde este repo, en `telegram-bot/n8n/`).
4. Abrí el nodo **"Llamar API transcripción"**: la URL ya viene configurada como `http://172.17.0.1:8765/transcribe` para que N8N (en Docker) llame a la API en el host. No hace falta cambiarla si la API corre en el mismo servidor.
5. **Activá el workflow** (toggle **Active** en ON).
6. En el nodo **"Webhook Recibir audio"** copiá la **Production URL**. Debería ser:  
   **http://104.225.140.9:5678/webhook/transcribir**

## 3. Configuración del bot (ya hecha)

En el `.env` del bot ya está:

```env
TRANSCRIBE_API_URL=http://104.225.140.9:5678/webhook/transcribir
```

Así el bot envía los audios al webhook de N8N y N8N los reenvía a la API de transcripción.

## 4. Reiniciar el bot

Si Jarvis ya estaba corriendo, reinicialo para que tome el `.env`:

```bash
# Si lo tenés en primer plano, Ctrl+C y de nuevo:
cd /root/telegram-bot
./venv/bin/python jarvis_bot.py
```

---

**Resumen:** Con la API en 8765 levantada, el workflow importado y activo en N8N, y el bot con `TRANSCRIBE_API_URL` apuntando al webhook, los mensajes de voz pasan por N8N y podés sumar ahí los pasos que quieras (logs, notificaciones, etc.).
