# Jarvis — agente Stable Diffusion en n8n

Workflow exportable: **`n8n/jarvis-stable-diffusion-agente.json`**.

## Estado en este servidor (VPS)

- El workflow **ya fue importado** en la instancia n8n Docker como id **`4`**, **activado** y el contenedor **`n8n`** se reinició para aplicar el cambio.
- Debería aparecer en **Chat → Workflow agents** como **Jarvis - Stable Diffusion** (tras refrescar la página).
- **Sin GPU en el VPS:** corre el servicio systemd **`sd-a1111-bridge`**, que escucha en **`172.17.0.1:7860`** (solo red Docker, no la IP pública) y traduce las rutas `/sdapi/v1/*` al estilo Automatic1111 hacia **OpenAI Images** (`dall-e-2` / `dall-e-3`) usando `OPENAI_API_KEY` de `/root/telegram-bot/.env`. Los nodos n8n siguen apuntando a `http://172.17.0.1:7860/...` sin cambios.
- **Img2img** usa `images.edit` de DALL·E 2 (512×512). **Interrogate** usa `gpt-4o-mini` con visión.
- Comandos útiles: `sudo systemctl status sd-a1111-bridge`, `sudo journalctl -u sd-a1111-bridge -f`.
- Variable opcional: `SD_BRIDGE_IMAGE_MODEL=dall-e-3` para forzar DALL·E 3 en txt2img (solo `n=1`).
- **`SD_BRIDGE_PUBLIC_BASE_URL`** + puerto **`SD_BRIDGE_PUBLIC_PORT`** (default `17860`): el puente guarda cada PNG y expone `GET /i/<uuid>.png` para que el **chat de n8n** pueda mostrar `![...](http://TU_IP:17860/i/....png)` (el data-URL gigante suele no renderizarse). Abrí el puerto en el firewall del VPS si el navegador no carga la imagen.

### Reimportar / reactivar (CLI)

```bash
cd /root/telegram-bot
bash n8n/import-jarvis-stable-diffusion.sh
```

(Ajustá el `id=4` en el script si en tu base el workflow tuvo otro identificador.)

## Qué hace

Agente de **Chat Hub** (`availableInChat: true`) que aparece en el **panel de agentes** de n8n. Usa **Google Gemini** como cerebro y llama a la API REST de **Automatic1111 Web UI** para:

| Herramienta | Endpoint A1111 |
|-------------|----------------|
| `sd_a1111_list_models` | `GET /sdapi/v1/sd-models` |
| `sd_a1111_get_options` | `GET /sdapi/v1/options` |
| `sd_a1111_txt2img` | `POST /sdapi/v1/txt2img` |
| `sd_a1111_img2img` | `POST /sdapi/v1/img2img` |
| `sd_a1111_interrogate` | `POST /sdapi/v1/interrogate` |

## Requisitos en el servidor

1. **Un backend en `172.17.0.1:7860`** que hable API estilo A1111: o bien **Automatic1111** real (`--api`), o en este VPS el servicio **`sd-a1111-bridge`** (OpenAI).
2. **`OPENAI_API_KEY`** en `/root/telegram-bot/.env` si usás el puente (facturación según tu cuenta OpenAI).

Si tu WebUI corre en otro host/puerto, abrí el workflow y reemplazá **todas** las URLs `http://172.17.0.1:7860` por la tuya (inversión de dependencias: el agente no debería “recordar” la URL en el prompt del sistema, pero los nodos HTTP sí deben apuntar bien).

### Si Stable Diffusion corre en tu PC y n8n en un VPS

`172.17.0.1` solo es la **puerta de enlace Docker del servidor donde corre n8n**. No apunta a tu PC. Opciones típicas: **Tailscale/ZeroTier**, **túnel inverso (ssh -R)**, o exponer el puerto de A1111 detrás de **VPN** — y entonces pegá esa URL base en los cinco nodos HTTP del workflow.

### Arranque mínimo de Automatic1111 (referencia)

En la máquina donde tengas el WebUI instalado, hace falta el modo API, por ejemplo flags del estilo `--api` y `--listen` (según tu instalación). El puerto por defecto suele ser **7860**.

## Importación en n8n

1. **Workflows → Import from File** → elegí `jarvis-stable-diffusion-agente.json`.
2. Revisá el nodo **Google Gemini Chat Model**: si tu instancia no tiene la misma credencial que en el export, asigná la tuya.
3. Activá el workflow (**Active** ON).
4. Abrí **Chat** / **Agents** en n8n y elegí **Jarvis - Stable Diffusion**.

## SOLID (cómo está pensado el workflow)

- **S:** cada herramienta HTTP cubre una sola operación de la API (listar, opciones, txt2img, img2img, interrogate).
- **O:** podés añadir nodos nuevos (p. ej. ControlNet, upscale) sin reescribir el mensaje del sistema del agente.
- **L:** si A1111 devuelve error JSON, el modelo debe relatar el fallo real, no inventar una imagen.
- **I:** el agente no está obligado a llamar a “listar modelos” en cada turno; solo cuando aporte valor.
- **D:** el detalle del backend (URL, auth futura) vive en los nodos, no en la lógica narrativa del LLM.

## Sobre “sin límites”

El workflow **no impone cupos** de generación: el tope lo marcan tu GPU, colas, tiempo y políticas de contenido que apliques vos. El system message del agente recuerda cumplir leyes y normas de uso.

## Notas img2img / interrogate

- Si el usuario **adjunta imagen** en el chat, los nodos intentan usar el binario `data0` (mismo patrón conceptual que el agente de transcripción con audio).
- Si no hay adjunto, se puede usar el parámetro de **base64 puro** descrito en cada herramienta.
