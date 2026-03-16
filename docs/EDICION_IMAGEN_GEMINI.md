# Edición de imágenes con Gemini (Nanobanana)

Podés enviar una foto o usar la última imagen que generó el bot y pedir que la edite con **Gemini Imagen** (modelo de edición).

## Cómo usar

1. **Enviar una foto**  
   Mandá cualquier foto al bot. La guarda como “última imagen” y te pide que le digas cómo editarla.

2. **Indicar la edición por texto o audio**  
   - Por texto: por ejemplo *«edita esta imagen: cambia el fondo a una playa»* o *«cambia el cielo a atardecer»*.  
   - Por audio: mandá un mensaje de voz con la misma idea (ej. *«edita esta imagen y poné un fondo de bosque»*).  
   - O usá el comando: **/editarimagen** + tu instrucción (ej. `/editarimagen poné un marco dorado`).

3. **Si el bot acaba de generar una imagen**  
   Esa imagen queda como “última”. Podés responder en texto o audio con algo como *«edita esta imagen: añadí nieve»* o usar `/editarimagen añadí nieve`.

## Requisitos

- **GEMINI_API_KEY** en el `.env` del servidor (misma key que para generar imágenes).
- Modelo de edición: **imagen-3.0-capability-001** (vía API de Google).

## Detalles

- La “última imagen” es la última foto que enviaste **o** la última imagen que generó el bot (por /imagen o por chat con IMAGEN:).
- Después de cada edición, la imagen editada pasa a ser la nueva “última”, así que podés encadenar varias ediciones.
- Frases que el bot interpreta como “editar imagen”: *edita esta imagen*, *editar la imagen*, *cambia esta imagen*, *modifica esta imagen*, etc., seguidas de tu instrucción.
