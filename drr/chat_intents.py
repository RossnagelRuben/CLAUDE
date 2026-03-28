"""Intenciones de chat compartidas (WhatsApp / Telegram)."""

import re

# Frases fijas (inicio del mensaje, tras quitar muletillas).
_EDIT_IMAGE_PREFIXES: tuple[str, ...] = (
    "edita esta imagen",
    "editar esta imagen",
    "edita la imagen",
    "editar la imagen",
    "edita la foto",
    "editar la foto",
    "cambia esta imagen",
    "modifica esta imagen",
    "editala",
    "edítala",
    "editame la imagen",
    "editame la foto",
    "edítame la imagen",
    "edítame la foto",
    "editá la imagen",
    "editá la foto",
    "editá esta imagen",
    "editá esta foto",
)

_LEAD_FILLER = re.compile(
    r"^(?:perfecto|ok|bueno|genial|dale|listo|gracias|muy bien|excelente|hola|holi|hey)\b\s*[,:\s-]+",
    re.IGNORECASE,
)

# "editame la imagen, agregar X" / "modificá la foto: ..."
_EDIT_INLINE = re.compile(
    r"(?is)\b(?:"
    r"edit(?:a|á|ar|ame|áme|emos)|"
    r"modific(?:a|á|ar|ame)|"
    r"cambi(?:a|á|ar|ame)"
    r")\s+(?:la\s+|esta\s+|mi\s+)?(?:imagen|foto|fotografía|fotografia)\b\s*[:,]?\s*(.*)$"
)

# Voz / lenguaje natural: «agrega un fondo X a la imagen», «poné un gato en la foto» (no dice «editame»).
_EDIT_REF_LAST_IMAGE = re.compile(
    r"(?is)^(?P<instr>.+?)\s+(?:a|en)\s+la\s+(?:imagen|fotografía|fotografia|foto)\s*\.?\s*$"
)
_EDIT_REF_LAST_IMAGE_VERB = re.compile(
    r"(?is)^(agrega|agregá|agregame|agregáme|añade|añadí|añadime|pon|poné|ponme|"
    r"coloca|colocá|colocame|mete|meté|incorpora|incorporá|"
    r"cambia|cambiá|cambiame|modifica|modificá|modificame|"
    r"haz|hacé|haceme|dale|dame)\b"
)

# Sin decir "edita la foto": se refiere a la última imagen del chat (WhatsApp/Telegram).
_EDIT_LAST_IMAGE_IMPLIED = re.compile(
    r"(?is)\b("
    r"qu[ií]t[aá]me\s+el\s+fondo|"
    r"qu[ií]t[aá]\s+el\s+fondo|"
    r"qu[ií]tarle\s+(?:por\s+favor\s+)?(?:todo\s+)?el\s+fondo|"
    r"quitarle\s+(?:por\s+favor\s+)?(?:todo\s+)?el\s+fondo|"
    r"qu[ií]tale\s+(?:por\s+favor\s+)?(?:todo\s+)?el\s+fondo|"
    r"quitar\s+(?:por\s+favor\s+)?(?:todo\s+)?el\s+fondo|"
    r"s[aá]c[aá]me\s+el\s+fondo|"
    r"s[aá]c[aá]\s+el\s+fondo|"
    r"sacarle\s+(?:por\s+favor\s+)?(?:todo\s+)?el\s+fondo|"
    r"sin\s+fondo|"
    r"sin\s+(?:ning[uú]n\s+)?fondo|"
    r"fondo\s+transparente|"
    r"elimin(?:a|á|e)\s+(?:por\s+favor\s+)?(?:todo\s+)?(?:el\s+)?fondo|"
    r"borr(?:a|á)\s+el\s+fondo|"
    r"remove\s+(?:the\s+)?background|"
    r"quitar\s+el\s+fondo|"
    r"sacar\s+el\s+fondo|"
    r"dej(?:a|á)\s+la\s+imagen\s+sin\s+fondo|"
    r"dej(?:a|á)\s+la\s+foto\s+sin\s+fondo"
    r")\b"
)

# Tamaño / zoom: solo si habla de la imagen o foto (evita «agranda el negocio»).
_EDIT_SIZE_WITH_MEDIA = re.compile(
    r"(?is)\b("
    r"agrand[aá]\w*|agrande|agranden|ampli[aá]\w*|ampliar\b|escal[aá]\w*|"
    r"achic[aá]\w*|reduc[ií]\w*|acerc[aá]\w*|alej[aá]\w*"
    r")\s+(?:un\s+poco\s+)?(?:la\s+|esta\s+|mi\s+)?(?:imagen|foto|fotografía|fotografia)\b"
)
_EDIT_SIZE_MEDIA_FIRST = re.compile(
    r"(?is)\b(?:imagen|foto|fotografía|fotografia)\b.+?\b("
    r"más\s+grande|más\s+chic[aá]|más\s+pequeñ[oa]|"
    r"agrand[aá]\w*|agrande|agranden|ampli[aá]\w*|escal[aá]\w*|achic[aá]\w*"
    r")\b"
)
_EDIT_SIZE_HACER = re.compile(
    r"(?is)\bhac(?:e|é|er|eme|éme)\w*\s+(?:la\s+|esta\s+)?(?:imagen|foto)\s+"
    r"(?:más\s+grande|más\s+chic[aá]|más\s+pequeñ[oa]|más\s+grande\s+y\s+más\s+clara)\b"
)

# «agrandala», «ampliála» (la última imagen sobreentendida).
_EDIT_SIZE_VERB_LA = re.compile(
    r"(?is)\b(agrand[aá]|ampli[aá]|escal[aá]|achic[aá]|reduc[ií]|acerc[aá]|alej[aá])la\b"
)


def parse_edit_image_intent(text: str) -> str | None:
    """
    Si el usuario quiere editar la última imagen con Gemini, devuelve el prompt de edición.
    Cubre frases como «perfecto, editame la imagen, agregar una jirafa» (no solo prefijos al inicio).
    """
    if not text or len(text) > 1500:
        return None
    raw = _LEAD_FILLER.sub("", text.strip()).strip()
    if not raw:
        return None
    low = raw.lower()

    for prefix in _EDIT_IMAGE_PREFIXES:
        pl = prefix.lower()
        if low.startswith(pl):
            rest = raw[len(prefix) :].strip().lstrip(":,").strip()
            return rest or raw

    if low.startswith("edita ") or low.startswith("editar "):
        rest = raw[7:].strip()
        if rest and ("imagen" in low[:40] or "foto" in low[:40] or "esta" in low[:25]):
            return rest

    m = _EDIT_INLINE.search(raw)
    if m:
        instr = (m.group(1) or "").strip()
        return instr if instr else raw

    if _EDIT_LAST_IMAGE_IMPLIED.search(raw):
        return raw.strip()

    m_ref = _EDIT_REF_LAST_IMAGE.match(raw)
    if m_ref:
        instr = (m_ref.group("instr") or "").strip()
        if len(instr) >= 6 and _EDIT_REF_LAST_IMAGE_VERB.match(instr):
            return instr

    if _EDIT_SIZE_WITH_MEDIA.search(raw):
        return raw.strip()
    if _EDIT_SIZE_MEDIA_FIRST.search(raw):
        return raw.strip()
    if _EDIT_SIZE_HACER.search(raw):
        return raw.strip()
    if _EDIT_SIZE_VERB_LA.search(raw):
        return raw.strip()

    return None


# Segunda edición por voz/texto: «perfecto, centra la imagen…» (no empieza con «editame la imagen»).
_FOLLOWUP_EDIT_HINT = re.compile(
    r"(?is)\b("
    r"centr(?:a|á|al|ar|ame)?|central\b|alinear|alineá|"
    r"t[ií]tul|texto|tipograf|etiqueta|etiquet|"
    r"recort|marco|borde|zoom|rotar|voltear|espejo|"
    r"ilumin|brillo|contraste|saturac|filtro|"
    r"agrand[aá]|agrande|agranden|ampli[aá]|ampliar|escal[aá]|achic[aá]|reduc[ií]|acerc[aá]|alej[aá]|"
    r"más\s+grande|más\s+chic|más\s+pequeñ|"
    r"coloc[aá]|pon[eé]|agreg[aá]|sac[aá]|quit[aá]|manten[eé]|dej[aá]"
    r")\b"
)
_REFUSAL_GENERATION = re.compile(
    r"(?is)\b("
    r"no\s+quiero\s+que\s+(me\s+)?gener|"
    r"no\s+me\s+gener|"
    r"no\s+generes|"
    r"sin\s+generar|"
    r"no\s+hace\s+falta\s+generar|"
    r"no\s+es\s+una\s+imagen\s+nueva"
    r")\b"
)


def parse_followup_last_image_edit_intent(text: str) -> str | None:
    """
    Mensaje de seguimiento cuando ya hay una «última imagen» en memoria:
    menciona imagen/foto y una acción visual (centrar, título, etc.).
    """
    if not text or len(text) > 1200:
        return None
    # No pisar el pedido de foto de un ítem del listado DRR («imagen del primer producto», «poné la foto del 2», etc.).
    if parse_producto_imagen_index(text) is not None:
        return None
    # Tampoco el comando de subir un producto al catálogo Meta/WhatsApp.
    if parse_upload_whatsapp_catalog_index(text) is not None:
        return None
    raw = _LEAD_FILLER.sub("", text.strip()).strip()
    if not raw:
        return None
    low = raw.lower()
    if _REFUSAL_GENERATION.search(raw):
        return None
    if not re.search(r"\b(imagen|foto|fotografía|fotografia)\b", low):
        return None
    if not _FOLLOWUP_EDIT_HINT.search(raw):
        return None
    return raw.strip()


_EXPLICIT_GENERATE_IMAGE = re.compile(
    r"(?is)("
    r"\bgener[áa](me|mos|te)?\s+(una\s+)?imagen\b|"
    r"\bgenerar\s+(una\s+)?imagen\b|"
    r"\bcre(ar|á)(me|mos)?\s+(una\s+)?imagen\b|"
    r"\bdibuj[áa](me|mos|te)?\s+"
    r"|^/imagen\b"
    r"|\bped(i|í)(me|mos)?\s+que\s+(me\s+)?gener"
    r"|\bhaceme\s+una\s+imagen\b"
    r"|\bquiero\s+una\s+imagen\s+(nueva|generada)\b"
    r")"
)


def user_requested_ai_image_generation(text: str) -> bool:
    """True solo si el usuario pidió explícitamente crear/generar una imagen (no edición)."""
    if not text or not str(text).strip():
        return False
    return bool(_EXPLICIT_GENERATE_IMAGE.search(text.strip()))


def parse_producto_imagen_index(text: str) -> int | None:
    """
    Si el usuario pide la imagen/foto de un producto del último listado DRR, devuelve índice 1-based.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if not any(k in t for k in ("imagen", "foto", "fotografía", "fotografia")):
        return None
    if re.search(r"\b(primer[oa]?|primero|primera|1\s*º|1\s*°|1er)\b", t):
        return 1
    m = re.search(r"\b(?:producto|n[º°]?|#)\s*(\d{1,3})\b", t)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b(?:imagen|foto)\s+(?:del\s+)?(?:el\s+)?(?:producto\s*)?(\d{1,3})\b", t)
    if m2:
        return int(m2.group(1))
    return None


# Guardar imagen editada en DRR (PATCH /Producto). Ver Swagger ProductoPatchRequest.
_SAVE_IMAGE_TO_DRR_INTENT = re.compile(
    r"(?is)\b("
    r"guard[áa]r?\s+(?:los\s+|las\s+)?cambios\b|"
    r"guard[áa]r?\s+cambios\b|"
    r"guard[áa]r?\s+(?:la\s+|esta\s+)?(?:imagen|foto)\b|"
    r"guard[áa]r?\s+(?:la\s+|esta\s+)?(?:imagen|foto)\s+en\s+(?:el\s+)?producto\b|"
    r"guard[áa]r?\s+en\s+(?:el\s+)?(?:producto|cat[aá]logo|drr)\b|"
    r"sub[ií](?:r|)\s+(?:la\s+|esta\s+)?(?:imagen|foto)\b|"
    r"actualiz[áa]r?\s+(?:la\s+)?(?:imagen|foto)\s+en\s+(?:el\s+)?producto\b|"
    r"persist[ií]r?\s+(?:la\s+)?(?:imagen|foto)\b|"
    r"grabar\s+(?:la\s+)?(?:imagen|foto)\b|"
    r"sub[ií]\s+al\s+producto\b|"
    r"mandar\s+(?:la\s+)?(?:imagen|foto)\s+al\s+producto\b"
    r")\b"
)


# Subir un ítem del último listado DRR al catálogo de WhatsApp (Meta Commerce), vía Graph API.
# Debe mencionar catálogo/whatsapp/meta para no confundir con «subir imagen al producto DRR».
_CATALOG_CONTEXT = re.compile(
    r"\b(cat[aá]logo|whatsapp\s+business|whatsapp|meta\s+commerce|commerce\s+manager)\b",
    re.IGNORECASE,
)
_CATALOG_UPLOAD_VERB = re.compile(
    r"\b(sub[ií]r?|publicar|agregar\s+a|cargar\s+en|mandar\s+a|pasar\s+a|enviar\s+a)\b",
    re.IGNORECASE,
)


def parse_upload_whatsapp_catalog_index(text: str) -> int | None:
    """
    Si el usuario pide subir/publicar un producto del último listado DRR al catálogo de WhatsApp
    (Meta), devuelve el índice 1-based dentro de ese listado.

    Ejemplos que matchean: «subir el primer producto al catálogo», «publicar producto 3 en whatsapp»,
    «mandar el segundo al catálogo de whatsapp business», «subir 2 al catálogo».
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 800:
        return None
    low = raw.lower()
    if not _CATALOG_CONTEXT.search(low):
        return None
    if not _CATALOG_UPLOAD_VERB.search(low):
        return None

    # «subir 2 al catálogo» / «publicar 3 a whatsapp»
    m_digit = re.search(
        r"\b(?:sub[ií]r?|publicar|mandar|pasar|enviar)\s+(?:el\s+|la\s+)?(\d{1,3})\s+(?:al|a)\s+(?:el\s+)?(?:cat[aá]logo|whatsapp)\b",
        low,
    )
    if m_digit:
        return int(m_digit.group(1))

    if re.search(r"\b(primer[oa]?|primero|primera|1\s*º|1\s*°|1er)\b", low):
        return 1
    if re.search(r"\b(segund[oa]?|2\s*º|2\s*°|2do|2da)\b", low):
        return 2
    if re.search(r"\b(tercer[oa]?|tercero|tercera|3\s*º|3\s*°|3er|3ro|3ra)\b", low):
        return 3
    if re.search(r"\b(cuart[oa]?|4\s*º|4\s*°|4to|4ta)\b", low):
        return 4
    if re.search(r"\b(quint[oa]?|5\s*º|5\s*°|5to|5ta)\b", low):
        return 5

    m_num = re.search(r"\b(?:producto|ítem|item|n[º°]?)\s*#?\s*(\d{1,3})\b", low)
    if m_num:
        return int(m_num.group(1))

    return None


def parse_save_image_to_drr_product_index(text: str) -> int | None:
    """
    Si el usuario pide guardar la imagen en DRR: devuelve índice 1-based del último listado,
    o -1 si debe usarse el producto asociado a la última imagen de catálogo mostrada (contexto).
    Si no es un pedido de guardado, devuelve None.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if not _SAVE_IMAGE_TO_DRR_INTENT.search(raw):
        return None
    low = raw.lower()
    if re.search(r"\b(primer[oa]?|primero|primera|1\s*º|1\s*°|1er)\b", low) and re.search(
        r"\b(producto|ítem|item)\b", low
    ):
        return 1
    m = re.search(r"\b(?:producto|ítem|item|n[º°]?)\s*#?\s*(\d{1,3})\b", low)
    if m:
        return int(m.group(1))
    return -1
