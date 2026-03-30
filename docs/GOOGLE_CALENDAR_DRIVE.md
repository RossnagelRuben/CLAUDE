# Google Calendar y Google Drive (Jarvis)

## ⚠️ Error “no cumple la política OAuth” / `400 invalid_request` con `http://104...`

Google **suele bloquear** la pantalla de login cuando la **URI de redirección** es **`http://` + IP pública** (o dominio sin HTTPS). No es un bug de Jarvis: es la política actual de Google para apps “web”.

**Qué hacer:** usá **HTTPS con un dominio** (gratis con [Let's Encrypt](https://letsencrypt.org/) + Certbot), por ejemplo `https://jarvis.tudominio.com/admin/google/oauth/callback`, y registrá **exactamente** esa URL en Google Cloud y en `GOOGLE_OAUTH_REDIRECT_URI`. Ejemplo de nginx: `deploy/nginx-jarvis-bridge-ssl.example.conf`.

**Sin comprar dominio:** podés usar **[sslip.io](https://sslip.io/)**: la IP `104.225.140.9` equivale al hostname `104-225-140-9.sslip.io` (guiones en lugar de puntos). En este VPS ya se puede haber configurado Certbot con ese nombre.

Alternativas: **Cloudflare Tunnel** (te da una URL `https://…`) o un túnel tipo **ngrok** solo para pruebas (la URI de redirect debe coincidir con la que te da el túnel).

El endpoint `GET /admin/google/status` (con token admin) incluye el campo **`oauth_redirect_warning`** si detecta este riesgo.

### Si ves `Error 400: redirect_uri_mismatch`

En **Google Cloud Console → Credenciales → tu cliente OAuth (Aplicación web)** → **URIs de redireccionamiento autorizados**, tiene que existir **exactamente** esta línea (copiar/pegar, sin espacios ni barra final de más):

`https://104-225-140-9.sslip.io/admin/google/oauth/callback`

Si solo tenés la URI vieja `http://104.225.140.9/...` **no sirve**: agregá la de arriba y **Guardá**. Luego abrí de nuevo:

`https://104-225-140-9.sslip.io/admin/google/oauth/start?token=TU_ADMIN_PANEL_TOKEN`

---

Google **no** usa una “API key” suelta para leer tu calendario o subir archivos a **tu** cuenta. Hace falta **OAuth 2.0**: vos autorizás a la aplicación una vez en el navegador y el servidor guarda un **token de actualización** en disco (`google_token.json`).

## 0. Cliente tipo «App de escritorio» (`installed` en el JSON)

Si descargaste un JSON con clave **`installed`** (solo `http://localhost` en `redirect_uris`), el bridge **igual** usa la URI que definís en `GOOGLE_OAUTH_REDIRECT_URI`.  
Si al autorizar Google devuelve **`redirect_uri_mismatch`**, ese cliente **no acepta** esa URL pública: creá en la misma consola un cliente **Aplicación web** con la URI exacta del callback y reemplazá el JSON (o usá `GOOGLE_OAUTH_CLIENT_ID` + `GOOGLE_OAUTH_CLIENT_SECRET` de ese cliente web en `.env`).

---

## 1. Crear proyecto y credenciales en Google Cloud

1. Entrá a [Google Cloud Console](https://console.cloud.google.com/).
2. Creá un proyecto nuevo (o elegí uno existente).
3. **APIs y servicios → Biblioteca** y habilitá:
   - **Google Calendar API**
   - **Google Drive API**
4. **APIs y servicios → Pantalla de consentimiento de OAuth**:
   - Tipo: **Externo** (si es tu uso personal está bien).
   - Completá nombre de la app, correo de soporte, dominio si te lo pide.
   - En **Ámbitos (scopes)** no hace falta listarlos a mano si usás el flujo estándar del código (Calendar eventos + Drive archivos creados por la app).
   - Agregá tu usuario de Google como **usuario de prueba** mientras la app esté en modo prueba.
5. **APIs y servicios → Credenciales → Crear credenciales → ID de cliente OAuth**:
   - Tipo de aplicación: **Aplicación web**.
   - **URI de redireccionamiento autorizado** (importante, debe coincidir **exactamente** con el servidor):

     Preferí **HTTPS + dominio**, ej. `https://jarvis.tudominio.com/admin/google/oauth/callback`  
     (HTTP con IP pública suele ser rechazado por Google; ver aviso arriba).

     La misma cadena exacta debe ir en `.env` como `GOOGLE_OAUTH_REDIRECT_URI`.

6. Descargá el JSON del cliente (cliente OAuth) y subilo al VPS, por ejemplo:

   `/root/telegram-bot/google_client_secret.json`

   En `.env`:

   ```env
   GOOGLE_OAUTH_CLIENT_SECRETS=/root/telegram-bot/google_client_secret.json
   GOOGLE_OAUTH_REDIRECT_URI=http://104.225.140.9/admin/google/oauth/callback
   JARVIS_PUBLIC_BASE_URL=http://104.225.140.9
   ```

   Ajustá IP o dominio según tu caso.

## 2. Instalar dependencias Python

En el entorno del bridge:

```bash
cd /root/telegram-bot
./venv/bin/pip install -r requirements-bridge.txt
```

## 2b. Varias cuentas de Gmail

- El enlace de autorización ahora pide **elegir cuenta** (`select_account`) y, si definís en `.env`  
  `GOOGLE_OAUTH_LOGIN_HINT=rubenrossnagel@gmail.com`, Google **sugiere** esa dirección.
- Podés abrir el enlace en **ventana de incógnito** y entrar solo con la cuenta que quieras.
- El token guardado en `google_token.json` es de **la cuenta con la que terminaste el login**; para cambiar de cuenta, borrá `google_token.json` en el servidor y volvé a pasar por `/admin/google/oauth/start`.

Si la app OAuth está en modo **Prueba**, en la pantalla de consentimiento tenés que agregar como **usuarios de prueba** cada Gmail que vaya a autorizar (por ejemplo `rubenrossnagel@gmail.com`).

---

## 2c. Diagnóstico si falla el login

- En el servidor: `tail -50 /root/telegram-bot/logs/google_oauth.jsonl` (una línea JSON por evento).
- Por HTTP (token admin): `GET /admin/google/oauth/log?limit=50&token=TU_ADMIN_PANEL_TOKEN`
- Variable `GOOGLE_OAUTH_DEBUG_LOG=0` desactiva escritura del JSONL.

El bridge corrige **PKCE**: el mismo `code_verifier` que se usa al armar la URL de Google se reutiliza al intercambiar el código (antes se perdía entre dos instancias de `Flow`).

---

## 3. Primera autorización (una vez)

1. Reiniciá el servicio del bridge tras cambiar `.env`.
2. En el navegador abrí (reemplazá el token por `ADMIN_PANEL_TOKEN` o `AGENT_SECRET` del `.env`):

   `http://104.225.140.9/admin/google/oauth/start?token=TU_TOKEN_ADMIN`

3. Iniciá sesión con tu cuenta de Google y aceptá permisos.
4. Deberías ver “Google conectado”. Se creará `google_token.json` en el proyecto (no lo subas a git).

Comprobación:

`GET /admin/google/status` con header `Authorization: Bearer TU_TOKEN_ADMIN`

o mirá `/status` del bridge: campos `google_oauth_client_configured` y `google_authorized`.

## 4. Uso desde WhatsApp / Telegram

- **Recordatorios con fecha/hora**: el modelo sigue las instrucciones de `agent_prompt.txt` (sección 8) y responde con `CALENDAR_PROPUESTA: {JSON}`. El bot pregunta si querés agendar; respondé **SI** o **NO**.
- **Subir a Drive**: el modelo usa `DRIVE_SUBIR: ruta/relativa`. En WhatsApp podés usar `DRIVE_SUBIR: ultimo` para el último archivo archivado en `wa_inbox/media/`.

Variables opcionales:

- `GOOGLE_CALENDAR_ID=primary` — calendario destino.
- `GOOGLE_CALENDAR_TIMEZONE=America/Argentina/Buenos_Aires` — zona de los eventos.
- `GOOGLE_DRIVE_FOLDER_ID=` — si querés que los archivos nuevos caigan en una carpeta concreta (ID de carpeta en Drive).

## 5. Seguridad

- El archivo JSON del cliente y `google_token.json` son sensibles: permisos restrictivos en disco (`chmod 600`) y no versionarlos.
- Las rutas `/admin/google/oauth/start` y `/admin/google/status` exigen el mismo token que el resto de paneles admin.
