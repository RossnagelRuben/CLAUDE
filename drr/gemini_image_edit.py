"""Edición de imágenes vía API Gemini (Developer API / AI Studio).

`models.edit_image` (Imagen 3 capability) solo está soportado en cliente Vertex AI,
no con API key. Para API key usamos `gemini-2.5-flash-image` (Nano Banana) con
`generateContent` y salida IMAGE.
"""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)


def _part_to_image_bytes(part) -> bytes | None:
    if getattr(part, "thought", None):
        return None
    inline = getattr(part, "inline_data", None)
    if inline is None:
        return None
    data = getattr(inline, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:
            return None
    return None


def _collect_images_from_response(response) -> list[bytes]:
    """Todas las imágenes inline en orden (varios modelos devuelven primero un eco de la entrada)."""
    out: list[bytes] = []
    if not response or not getattr(response, "candidates", None):
        return out
    for cand in response.candidates:
        content = getattr(cand, "content", None)
        if not content:
            continue
        for part in content.parts or []:
            b = _part_to_image_bytes(part)
            if b:
                out.append(b)
    return out


def _pick_edited_image(reference: bytes, candidates: list[bytes]) -> bytes | None:
    """
    Elige la imagen editada real. Si hay varias, la primera suele ser copia de la entrada;
    nos quedamos con la última parte distinta (en bytes) de la referencia.
    """
    if not candidates:
        return None
    ref = reference or b""
    distinct = [b for b in candidates if b != ref]
    if len(candidates) >= 2 and distinct:
        chosen = distinct[-1]
        logger.info(
            "Gemini edición: %d imagen(es) en la respuesta; usando la última distinta de la entrada "
            "(%d B ref → %d B salida).",
            len(candidates),
            len(ref),
            len(chosen),
        )
        return chosen
    if len(candidates) >= 2 and not distinct:
        logger.warning(
            "Gemini devolvió %d imágenes pero todas coinciden en bytes con la entrada.",
            len(candidates),
        )
        return None
    only = candidates[0]
    if only == ref:
        logger.warning(
            "Gemini devolvió una sola imagen idéntica en bytes a la entrada (sin edición aparente)."
        )
        return None
    return only


def gemini_edit_image_bytes(
    *,
    api_key: str,
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
) -> bytes | None:
    """
    Edición asistida por imagen + texto usando modelos Gemini con salida nativa de imagen.
    Con API key de AI Studio: prioriza gemini-2.5-flash-image.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    mime = (mime_type or "image/jpeg").strip() or "image/jpeg"

    instruccion = (
        "Sos un asistente de edición de imágenes. Te paso la imagen a modificar. "
        "Aplicá la instrucción de forma visible en el píxel (no devuelvas la misma imagen sin cambios). "
        "Si pedís quitar el fondo, el fondo debe ser transparente o uniforme según corresponda. "
        "Devolvé una sola imagen PNG/JPEG con el resultado final. Instrucción: "
        + prompt
    )

    parts = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime),
        types.Part.from_text(text=instruccion),
    ]

    env_model = os.getenv("GEMINI_IMAGE_EDIT_MODEL", "").strip()
    model_candidates = [
        m
        for m in (
            env_model,
            "gemini-2.5-flash-image",
            "gemini-2.5-flash-image-preview",
            "gemini-3.1-flash-image-preview",
        )
        if m
    ]

    for gm in model_candidates:
        for modalities in (["TEXT", "IMAGE"], ["IMAGE"]):
            try:
                response = client.models.generate_content(
                    model=gm,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_modalities=modalities,
                    ),
                )
                imgs = _collect_images_from_response(response)
                out = _pick_edited_image(image_bytes, imgs)
                if out:
                    return out
                txt = (getattr(response, "text", None) or "").strip()
                if txt:
                    logger.warning(
                        "Gemini (%s) modalities=%s: sin imagen útil. Texto head=%r",
                        gm,
                        modalities,
                        txt[:200],
                    )
            except Exception as e:
                logger.warning(
                    "Gemini edición generate_content (%s) modalities=%s: %s",
                    gm,
                    modalities,
                    e,
                )

    # Imagen edit_image: normalmente requiere Vertex AI, no API key de AI Studio.
    if os.getenv("GEMINI_VERTEX_IMAGEN_EDIT", "").strip().lower() in ("1", "true", "yes"):
        ref_image = types.RawReferenceImage(
            reference_id=1,
            reference_image=types.Image(image_bytes=image_bytes, mime_type=mime),
        )
        try:
            response = client.models.edit_image(
                model="imagen-3.0-capability-001",
                prompt=prompt,
                reference_images=[ref_image],
            )
            if (
                response.generated_images
                and response.generated_images[0].image
                and response.generated_images[0].image.image_bytes
            ):
                return response.generated_images[0].image.image_bytes
        except Exception as e:
            logger.warning("Gemini edit_image (Vertex): %s", e)

    return None
