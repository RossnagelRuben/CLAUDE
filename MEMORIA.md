# Memoria y logs de Jarvis

## Cómo funciona

- **Texto y voz**: Podés escribir o enviar mensajes de voz; los audios se transcriben con **faster_whisper** y se procesan igual que el texto. Todo queda registrado en el log (las entradas de voz se marcan como `voz`).
- **Log diario**: Cada día se crea un archivo `YYYY-MM-DD.md` con toda la conversación (mensajes, notas, comandos ejecutados, voz transcrita).
- **Contexto para Claude**: Los últimos 3 días de log se envían como contexto a Claude, así puede resumir y recordar lo que hablaste.
- **Nextcloud**: Si querés que los logs se sincronicen con Nextcloud, en tu `.env` agregá:

  ```
  NEXTCLOUD_DIR=/ruta/a/tu/carpeta/Nextcloud/Jarvis
  ```

  Esa carpeta tiene que ser la que sincronizás con Nextcloud (cliente de escritorio o montaje). Jarvis escribirá ahí `YYYY-MM-DD.md` y la subcarpeta `proyectos/`.

## Comandos

| Comando | Uso |
|--------|-----|
| `/log` | Ver el log de hoy |
| `/log 2026-03-14` | Ver el log de una fecha |
| `/dias` | Listar fechas que tienen log |
| `/resumen` | Resumen del día (Claude resume el log de hoy) |
| `/resumen 3` | Resumen de los últimos 3 días (máx. 7) |
| `/proyecto Nombre \| Descripción` | Crear un proyecto en `proyectos/Nombre.md` |
| `/audio <texto>` | Generar nota de voz (TTS) con el texto — usa **edge_tts** (sin API key) |
| `/imagen <descripción>` | Generar imagen con **Gemini (Imagen)** o **OpenAI DALL·E 3** (`GEMINI_API_KEY` y/o `OPENAI_API_KEY` en `.env`) |

## Audio e imágenes

- **/audio**: Convierte texto a voz (español Argentina, edge_tts). No requiere API key.
- **/imagen**: Genera una imagen con IA. Se usa **Gemini (Imagen)** si tenés `GEMINI_API_KEY` en `.env`; si falla o no está, se usa **OpenAI DALL·E 3** con `OPENAI_API_KEY`. Podés tener una o ambas claves.

## Apuntes y proyectos

- Decir "guardá esto como nota" o "tomá apunte de X" hace que Jarvis responda con `NOTA: ...` y se guarde en el log y en `notes/`.
- Los proyectos creados con `/proyecto` quedan en `proyectos/` (o en tu carpeta Nextcloud si definiste `NEXTCLOUD_DIR`).
