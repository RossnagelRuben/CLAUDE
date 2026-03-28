"""Generación de imagen (Imagen / DALL·E) usando variables de entorno."""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

_GEMINI_IMAGEN_MODELS: tuple[str, ...] = (
    "imagen-4.0-generate-001",
    "imagen-4.0-fast-generate-001",
    "imagen-3.0-generate-002",
)


def generate_image_bytes_env(prompt: str) -> bytes | None:
    """
    Usa GEMINI_API_KEY (Imagen) y si falla OPENAI_API_KEY (DALL·E 3).
    Misma prioridad de modelos que whatsapp_bridge / jarvis_bot.
    """
    prompt = (prompt or "").strip()[:1000]
    if not prompt:
        return None
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if gemini_key:
        try:
            from google import genai
            from google.genai import types

            client_gemini = genai.Client(api_key=gemini_key)
            for model_name in _GEMINI_IMAGEN_MODELS:
                try:
                    response = client_gemini.models.generate_images(
                        model=model_name,
                        prompt=prompt,
                        config=types.GenerateImagesConfig(number_of_images=1),
                    )
                    if (
                        response.generated_images
                        and response.generated_images[0].image
                        and response.generated_images[0].image.image_bytes
                    ):
                        logger.info("Imagen generada (Gemini %s)", model_name)
                        return response.generated_images[0].image.image_bytes
                except Exception as e:
                    logger.warning("Gemini Imagen (%s) falló: %s", model_name, e)
        except Exception as e:
            logger.warning("Gemini Imagen falló: %s", e)
    if openai_key:
        try:
            from openai import OpenAI

            client_openai = OpenAI(api_key=openai_key)
            response = client_openai.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
                response_format="b64_json",
            )
            logger.info("Imagen generada (OpenAI dall-e-3)")
            return base64.b64decode(response.data[0].b64_json)
        except Exception as e:
            logger.exception("OpenAI imagen falló: %s", e)
    return None
