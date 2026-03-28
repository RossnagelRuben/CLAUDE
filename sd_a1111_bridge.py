"""
Puente HTTP compatible con Automatic1111 WebUI API (/sdapi/v1/*).

Motivo: este VPS no tiene GPU ni RAM para correr Stable Diffusion local.
Expone el mismo contrato que esperan los nodos n8n y delega en OpenAI Images / visión.

Variables de entorno:
  OPENAI_API_KEY (obligatoria)
  SD_BRIDGE_IMAGE_MODEL=dall-e-2|dall-e-3  (opcional, default dall-e-2 para 512 y lotes)
  SD_BRIDGE_PUBLIC_BASE_URL  (ej. http://TU_IP:17860) — si está definida, txt2img/img2img
    añaden chat_preview_urls para que n8n muestre la imagen en el chat vía markdown
  SD_BRIDGE_IMAGE_DIR  (opcional, default data/sd-bridge-images bajo telegram-bot)
"""
from __future__ import annotations

import base64
import io
import os
import re
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import OpenAI
from PIL import Image

app = FastAPI(title="A1111 API bridge (OpenAI)", version="1.0.0")
public_app = FastAPI(title="SD bridge public images", version="1.0.0")

_BRIDGE_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_IMAGE_DIR = os.path.join(_BRIDGE_ROOT, "data", "sd-bridge-images")
_SAFE_PNG = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\.png$")


def _image_dir() -> str:
    return os.environ.get("SD_BRIDGE_IMAGE_DIR", _DEFAULT_IMAGE_DIR)


def _persist_pngs_from_b64(images_b64: list[str]) -> list[str]:
    """
    Guarda cada PNG y devuelve URLs públicas (si SD_BRIDGE_PUBLIC_BASE_URL está definida).
    """
    base = (os.environ.get("SD_BRIDGE_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not base or not images_b64:
        return []
    d = _image_dir()
    os.makedirs(d, exist_ok=True)
    out: list[str] = []
    for raw_b64 in images_b64:
        name = f"{uuid.uuid4()}.png"
        path = os.path.join(d, name)
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except Exception:
            continue
        with open(path, "wb") as f:
            f.write(data)
        out.append(f"{base}/i/{name}")
    return out


@public_app.get("/i/{filename}")
def serve_generated_png(filename: str) -> FileResponse:
    """Solo UUID.png — para que el chat de n8n cargue la imagen desde el navegador."""
    if not _SAFE_PNG.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(_image_dir(), filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/png")


@public_app.get("/health")
def public_health() -> dict[str, str]:
    return {"status": "ok", "role": "public-images"}

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise HTTPException(
                status_code=503,
                detail="OPENAI_API_KEY no configurada en el servicio sd-a1111-bridge",
            )
        _client = OpenAI(api_key=key)
    return _client


def _strip_data_url(b64: str) -> str:
    s = (b64 or "").strip()
    if "base64," in s:
        s = s.split("base64,", 1)[1]
    return re.sub(r"\s+", "", s)


def _pick_openai_size(width: int, height: int, prefer_dalle3: bool) -> tuple[str, str]:
    """Devuelve (size_string, model)."""
    w, h = max(256, min(width, 1024)), max(256, min(height, 1024))
    env_model = (os.environ.get("SD_BRIDGE_IMAGE_MODEL") or "").strip().lower()
    if env_model in ("dall-e-3", "dalle3"):
        if w >= h * 1.2:
            return "1792x1024", "dall-e-3"
        if h >= w * 1.2:
            return "1024x1792", "dall-e-3"
        return "1024x1024", "dall-e-3"
    if prefer_dalle3 and w >= 1024 and h >= 1024:
        return "1024x1024", "dall-e-3"
    # dall-e-2 tamaños fijos
    candidates = [("512x512", 512, 512), ("256x256", 256, 256), ("1024x1024", 1024, 1024)]
    best = min(candidates, key=lambda t: abs(w - t[1]) + abs(h - t[2]))
    return best[0], "dall-e-2"


def _build_prompt(prompt: str, negative: str) -> str:
    p = (prompt or "").strip()
    n = (negative or "").strip()
    if n:
        p = f"{p}\n\nAvoid: {n}"
    return p or "abstract art"


@app.get("/sdapi/v1/sd-models")
def list_models() -> list[dict[str, Any]]:
    """Contrato mínimo A1111: lista de checkpoints."""
    return [
        {
            "title": "openai-bridge (dall-e-2)",
            "model_name": "openai-bridge-dalle2",
            "hash": "bridge",
            "sha256": "",
            "filename": "openai-bridge",
            "config": "",
        },
        {
            "title": "openai-bridge (dall-e-3)",
            "model_name": "openai-bridge-dalle3",
            "hash": "bridge",
            "sha256": "",
            "filename": "openai-bridge-dalle3",
            "config": "",
        },
    ]


@app.get("/sdapi/v1/options")
def get_options() -> dict[str, Any]:
    return {
        "sd_model_checkpoint": "openai-bridge",
        "sd_vae": "Automatic",
    }


@app.post("/sdapi/v1/txt2img")
def txt2img(body: dict[str, Any]) -> dict[str, Any]:
    prompt = _build_prompt(
        str(body.get("prompt", "")),
        str(body.get("negative_prompt", "")),
    )
    width = int(body.get("width") or 512)
    height = int(body.get("height") or 512)
    batch_size = max(1, min(int(body.get("batch_size") or 1), 4))
    size, model = _pick_openai_size(width, height, prefer_dalle3=False)

    client = get_client()
    images_b64: list[str] = []

    if model == "dall-e-3":
        if batch_size > 1:
            raise HTTPException(
                status_code=400,
                detail="dall-e-3 solo admite batch_size=1; usá dall-e-2 o batch 1",
            )
        r = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:4000],
            size=size,  # type: ignore[arg-type]
            quality="standard",
            response_format="b64_json",
            n=1,
        )
        if r.data and r.data[0].b64_json:
            images_b64.append(r.data[0].b64_json)
    else:
        r = client.images.generate(
            model="dall-e-2",
            prompt=prompt[:1000],
            size=size,  # type: ignore[arg-type]
            response_format="b64_json",
            n=batch_size,
        )
        for item in r.data or []:
            if item.b64_json:
                images_b64.append(item.b64_json)

    if not images_b64:
        raise HTTPException(status_code=502, detail="OpenAI no devolvió imágenes")

    preview = _persist_pngs_from_b64(images_b64)
    return {
        "images": images_b64,
        "parameters": body,
        "info": f"openai-bridge model={model} size={size}",
        # n8n Chat muestra mal data-URLs enormes; el agente debe usar estas URLs en markdown
        "chat_preview_urls": preview,
    }


@app.post("/sdapi/v1/img2img")
def img2img(body: dict[str, Any]) -> dict[str, Any]:
    """Usa images.edit (DALL-E 2) con PNG cuadrado si es posible."""
    inits = body.get("init_images") or []
    if not inits or not str(inits[0]).strip():
        raise HTTPException(
            status_code=400,
            detail="init_images[0] requerido (base64 de la imagen)",
        )
    raw = _strip_data_url(str(inits[0]))
    try:
        img_bytes = base64.b64decode(raw, validate=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"base64 inválido: {e}") from e

    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"No es imagen válida: {e}") from e

    # Edits API: imagen PNG; redimensionar a cuadrado permitido
    side = 512
    im = im.resize((side, side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    buf.seek(0)
    buf.name = "image.png"

    prompt = _build_prompt(
        str(body.get("prompt", "")),
        str(body.get("negative_prompt", "")),
    )
    client = get_client()

    # API OpenAI: images.edit con dall-e-2
    r = client.images.edit(
        model="dall-e-2",
        image=buf,
        prompt=prompt[:1000],
        n=1,
        size="512x512",
        response_format="b64_json",
    )
    if not r.data or not r.data[0].b64_json:
        raise HTTPException(status_code=502, detail="OpenAI edit no devolvió imagen")

    b64s = [r.data[0].b64_json]
    preview = _persist_pngs_from_b64(b64s)
    return {
        "images": b64s,
        "parameters": body,
        "info": "openai-bridge img2img=dall-e-2-edit",
        "chat_preview_urls": preview,
    }


@app.post("/sdapi/v1/interrogate")
def interrogate(body: dict[str, Any]) -> dict[str, Any]:
    """CLIP-like: descripción corta vía visión OpenAI."""
    raw = _strip_data_url(str(body.get("image", "")))
    if not raw:
        raise HTTPException(status_code=400, detail="campo image (base64) requerido")
    try:
        base64.b64decode(raw, validate=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"base64 inválido: {e}") from e

    model = str(body.get("model") or "clip").lower()
    client = get_client()
    # gpt-4o-mini multimodal
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Reply with a single line of comma-separated booru-style tags "
                        "(English, lowercase), max 40 tags, no explanation.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{raw}",
                        },
                    },
                ],
            }
        ],
        max_tokens=300,
    )
    text = (completion.choices[0].message.content or "").strip()
    return {"caption": text, "model": model}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "backend": "openai-bridge"}

