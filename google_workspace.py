"""
Google Calendar + Google Drive mediante OAuth2 (cuenta del usuario).

No usa una "API key" suelta: en Google Cloud creás credenciales OAuth tipo "Aplicación web",
descargás el JSON del cliente y lo referenciás con GOOGLE_OAUTH_CLIENT_SECRETS.
El token de acceso se guarda en disco (google_token.json) tras autorizar en el navegador.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]

_BASE = Path(__file__).resolve().parent


def _secrets_path() -> Path | None:
    p = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS") or "").strip()
    if not p:
        return None
    path = Path(p).expanduser()
    return path if path.is_file() else None


def token_path() -> Path:
    custom = (os.getenv("GOOGLE_TOKEN_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return _BASE / "google_token.json"


def redirect_uri() -> str:
    return (os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or "").strip() or (
        "http://127.0.0.1:8766/admin/google/oauth/callback"
    )


def oauth_redirect_uri_warning() -> str | None:
    """
    Google suele rechazar (400 invalid_request / política) redirects OAuth HTTP que no sean loopback.
    Devuelve texto de aviso o None si la URI parece aceptable.
    """
    rid = (redirect_uri() or "").strip()
    low = rid.lower()
    if not low:
        return None
    if low.startswith("https://"):
        return None
    if low.startswith("http://localhost") or low.startswith("http://127.0.0.1"):
        return None
    if low.startswith("http://"):
        return (
            "Google OAuth suele BLOQUEAR redirect HTTP en IP o dominio público (no localhost). "
            "Solución: dominio + certificado TLS (Let's Encrypt), misma URL en Google Cloud y en "
            "GOOGLE_OAUTH_REDIRECT_URI=https://tu-dominio/admin/google/oauth/callback. "
            "Ver docs/GOOGLE_CALENDAR_DRIVE.md y deploy/nginx-jarvis-bridge-ssl.example.conf."
        )
    return None


def calendar_id() -> str:
    return (os.getenv("GOOGLE_CALENDAR_ID") or "primary").strip() or "primary"


def default_timezone() -> str:
    return (os.getenv("GOOGLE_CALENDAR_TIMEZONE") or "America/Argentina/Buenos_Aires").strip()


def drive_folder_id() -> str | None:
    s = (os.getenv("GOOGLE_DRIVE_FOLDER_ID") or "").strip()
    return s or None


def _oauth_from_env() -> dict[str, Any] | None:
    cid = (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    csec = (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    if not cid or not csec:
        return None
    rid = redirect_uri()
    return {
        "web": {
            "client_id": cid,
            "client_secret": csec,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [rid],
        }
    }


def _oauth_client_config_root() -> dict[str, Any]:
    """
    Dict con clave 'web' para google_auth_oauthlib.flow.Flow.from_client_config.
    Acepta JSON tipo aplicación web o tipo installed (escritorio) — en ambos casos
    se usa GOOGLE_OAUTH_REDIRECT_URI como única URI de callback.
    """
    env_cfg = _oauth_from_env()
    if env_cfg:
        return env_cfg
    sp = _secrets_path()
    if not sp:
        raise RuntimeError("Falta GOOGLE_OAUTH_CLIENT_SECRETS o GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET")
    raw = json.loads(sp.read_text(encoding="utf-8"))
    rid = redirect_uri()
    if "web" in raw and isinstance(raw["web"], dict):
        w = dict(raw["web"])
        w["redirect_uris"] = [rid]
        return {"web": w}
    if "installed" in raw and isinstance(raw["installed"], dict):
        inst = raw["installed"]
        return {
            "web": {
                "client_id": inst["client_id"],
                "client_secret": inst["client_secret"],
                "auth_uri": inst.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": inst.get("token_uri", "https://oauth2.googleapis.com/token"),
                "redirect_uris": [rid],
            }
        }
    raise RuntimeError("JSON OAuth inválido: necesita clave 'web' o 'installed'")


def _oauth_debug_enabled() -> bool:
    return (os.getenv("GOOGLE_OAUTH_DEBUG_LOG", "1").strip().lower() in ("1", "true", "yes", "si", "sí"))


def oauth_debug_log(event: str, **fields: Any) -> None:
    """JSONL en logs/google_oauth.jsonl para diagnosticar OAuth (sin secretos largos)."""
    if not _oauth_debug_enabled():
        return
    try:
        log_dir = _BASE / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        safe: dict[str, Any] = {}
        for k, v in fields.items():
            if k in ("code", "authorization_code") and isinstance(v, str) and len(v) > 12:
                safe[k] = v[:8] + "…"
            elif k == "client_secret" or (isinstance(v, str) and "GOCSPX" in v):
                safe[k] = "(redacted)"
            else:
                safe[k] = v
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **safe,
        }
        path = log_dir / "google_oauth.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        logger.warning("oauth_debug_log: %s", ex)


def _make_flow(*, code_verifier: str | None = None) -> Flow:
    kw: dict[str, Any] = {"redirect_uri": redirect_uri()}
    if code_verifier is not None:
        kw["code_verifier"] = code_verifier
        kw["autogenerate_code_verifier"] = False
    return Flow.from_client_config(
        _oauth_client_config_root(),
        scopes=SCOPES,
        **kw,
    )


def oauth_configured() -> bool:
    if _oauth_from_env() is not None:
        return True
    return _secrets_path() is not None


def load_credentials() -> Credentials | None:
    path = token_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    except Exception as e:
        logger.warning("No se pudo leer google token: %s", e)
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            save_credentials(creds)
        except Exception as e:
            logger.warning("Refresh token Google falló: %s", e)
            return None
    if not creds or not creds.valid:
        return None
    return creds


def save_credentials(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_authorized() -> bool:
    c = load_credentials()
    return c is not None and c.valid


# Estado OAuth en disco (no solo RAM): evita "state inválido" si hay 2 workers,
# reinicio liviano, o se pierde el dict en memoria.
_OAUTH_PENDING_FILE = _BASE / "logs" / "oauth_pending_states.json"
_OAUTH_LOCK_FILE = _BASE / "logs" / ".oauth_state.lock"
_STATE_TTL = 1800.0  # 30 min


@contextmanager
def _oauth_pending_lock():
    _OAUTH_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(_OAUTH_LOCK_FILE, "a+b")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _read_pending_raw() -> dict[str, Any]:
    if not _OAUTH_PENDING_FILE.exists():
        return {}
    try:
        raw = json.loads(_OAUTH_PENDING_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_pending_raw(data: dict[str, Any]) -> None:
    _OAUTH_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _OAUTH_PENDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(_OAUTH_PENDING_FILE)
    try:
        os.chmod(_OAUTH_PENDING_FILE, 0o600)
    except OSError:
        pass


def _purge_pending_dict(data: dict[str, Any]) -> None:
    now = time.time()
    for k in list(data.keys()):
        try:
            if now - float((data[k] or {}).get("t", 0)) > _STATE_TTL:
                del data[k]
        except (TypeError, ValueError):
            del data[k]


def register_oauth_state() -> str:
    with _oauth_pending_lock():
        data = _read_pending_raw()
        _purge_pending_dict(data)
        st = secrets.token_urlsafe(32)
        data[st] = {"t": time.time(), "code_verifier": None}
        _write_pending_raw(data)
    oauth_debug_log("oauth_state_registered", state_prefix=st[:10], persisted=True)
    return st


def consume_oauth_state(state: str) -> str | None:
    """
    Valida state y devuelve el code_verifier PKCE guardado al armar la URL.
    None = state inválido o expirado.
    """
    with _oauth_pending_lock():
        data = _read_pending_raw()
        _purge_pending_dict(data)
        if not state or state not in data:
            _write_pending_raw(data)
            oauth_debug_log("oauth_state_missing_or_unknown", state_prefix=(state or "")[:10])
            return None
        entry = data[state]
        if time.time() - float(entry.get("t", 0)) > _STATE_TTL:
            del data[state]
            _write_pending_raw(data)
            oauth_debug_log("oauth_state_expired", state_prefix=state[:10])
            return None
        verifier = entry.get("code_verifier")
        if not isinstance(verifier, str) or not verifier:
            oauth_debug_log("oauth_state_no_pkce_verifier", state_prefix=state[:10], has_entry=True)
            _write_pending_raw(data)
            return None
        del data[state]
        _write_pending_raw(data)
    oauth_debug_log("oauth_state_consumed_ok", state_prefix=state[:10])
    return verifier


def oauth_login_hint() -> str:
    """Email opcional para sugerir cuenta en la pantalla de Google (varias cuentas abiertas)."""
    return (os.getenv("GOOGLE_OAUTH_LOGIN_HINT") or "").strip()


def build_authorization_url(state: str) -> str:
    if not oauth_configured():
        raise RuntimeError(
            "Falta OAuth: GOOGLE_OAUTH_CLIENT_SECRETS o GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET"
        )
    flow = _make_flow()
    # select_account: elige con qué Gmail autorizar; consent: refresco offline cuando aplica.
    auth_kw: dict[str, Any] = {
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent",
        "state": state,
    }
    hint = oauth_login_hint()
    if hint:
        auth_kw["login_hint"] = hint
    url, returned_state = flow.authorization_url(**auth_kw)
    # Google / oauthlib deben devolver el mismo state; si no, guardamos el que venga en la URL.
    state_key = returned_state or state
    with _oauth_pending_lock():
        data = _read_pending_raw()
        _purge_pending_dict(data)
        if state not in data:
            raise RuntimeError(
                "State OAuth no registrado o expiró. Volvé a abrir /admin/google/oauth/start con token admin."
            )
        if state_key != state:
            data[state_key] = data.pop(state)
        if state_key not in data:
            raise RuntimeError("State OAuth inconsistente tras authorization_url.")
        data[state_key]["code_verifier"] = flow.code_verifier
        _write_pending_raw(data)
    cfg = _oauth_client_config_root()
    cid = (cfg.get("web") or {}).get("client_id", "")[:20]
    warn = oauth_redirect_uri_warning()
    oauth_debug_log(
        "oauth_authorize_url_built",
        redirect_uri=redirect_uri(),
        client_id_prefix=cid,
        auth_url_path=str(url).split("?")[0][-60:],
        has_code_challenge="code_challenge=" in url,
        policy_warning=warn[:200] if warn else "",
    )
    return url


def finish_oauth_authorization_response(full_url: str, *, code_verifier: str) -> Credentials:
    """Intercambia el código de la URL de callback por credenciales (requiere PKCE verifier del start)."""
    if not oauth_configured():
        raise RuntimeError("Falta configuración OAuth (secrets o variables de entorno)")
    flow = _make_flow(code_verifier=code_verifier)
    try:
        flow.fetch_token(authorization_response=full_url)
    except Exception as e:
        oauth_debug_log(
            "oauth_fetch_token_error",
            error_type=type(e).__name__,
            error_msg=str(e)[:500],
        )
        raise
    creds = flow.credentials
    save_credentials(creds)
    oauth_debug_log("oauth_token_saved_ok", token_path=str(token_path()))
    return creds


def _parse_iso_to_aware(iso_s: str, tz_name: str) -> datetime:
    s = (iso_s or "").strip()
    if not s:
        raise ValueError("fecha vacía")
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)  # +0000 -> +00:00
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt


def create_calendar_event(
    title: str,
    start_iso: str,
    end_iso: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    creds = load_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("Google Calendar no autorizado. Abrí /admin/google/oauth/start con token admin.")

    tz_name = default_timezone()
    start = _parse_iso_to_aware(start_iso, tz_name)
    if end_iso:
        end = _parse_iso_to_aware(end_iso, tz_name)
    else:
        end = start + timedelta(hours=1)

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    body: dict[str, Any] = {
        "summary": (title or "Recordatorio")[:1024],
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
    }
    if description:
        body["description"] = description[:8000]

    ev = (
        service.events()
        .insert(calendarId=calendar_id(), body=body, sendUpdates="none")
        .execute()
    )
    return {
        "id": ev.get("id"),
        "htmlLink": ev.get("htmlLink"),
        "summary": ev.get("summary"),
    }


def upload_file_to_drive(
    local_path: Path,
    *,
    drive_name: str | None = None,
    folder_id: str | None = None,
) -> dict[str, Any]:
    creds = load_credentials()
    if not creds or not creds.valid:
        raise RuntimeError("Google Drive no autorizado.")

    path = local_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))

    name = drive_name or path.name
    parents = []
    fid = folder_id or drive_folder_id()
    if fid:
        parents = [fid]

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta: dict[str, Any] = {"name": name[:256]}
    if parents:
        meta["parents"] = parents

    media = MediaFileUpload(str(path), resumable=True)
    f = (
        service.files()
        .create(body=meta, media_body=media, fields="id,name,webViewLink,mimeType")
        .execute()
    )
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "webViewLink": f.get("webViewLink"),
        "mimeType": f.get("mimeType"),
    }


def extract_json_after_marker(text: str, marker: str) -> dict[str, Any] | None:
    """Busca `MARKER` y parsea el primer objeto JSON `{...}` que sigue."""
    t = text or ""
    idx = t.upper().find(marker.upper())
    if idx < 0:
        return None
    rest = t[idx + len(marker) :].strip()
    if not rest.startswith("{"):
        return None
    depth = 0
    for i, c in enumerate(rest):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = rest[: i + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    return None
    return None


def strip_marker_and_json(text: str, marker: str) -> str:
    t = text or ""
    idx = t.upper().find(marker.upper())
    if idx < 0:
        return t.strip()
    rest = t[idx + len(marker) :].strip()
    if not rest.startswith("{"):
        return t.strip()
    depth = 0
    for i, c in enumerate(rest):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                tail = rest[i + 1 :].strip()
                head = t[:idx].strip()
                return "\n\n".join(x for x in (head, tail) if x).strip()
    return t.strip()


def normalize_calendar_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Valida y devuelve title, start, end?, description?."""
    title = str(raw.get("title") or raw.get("summary") or "Recordatorio").strip()
    start = str(raw.get("start") or raw.get("start_iso") or "").strip()
    if not start:
        raise ValueError("Falta start o start_iso en JSON")
    end = raw.get("end") or raw.get("end_iso")
    end_s = str(end).strip() if end else None
    desc = raw.get("description")
    desc_s = str(desc).strip() if desc else None
    return {"title": title, "start": start, "end": end_s, "description": desc_s}


def format_event_for_user(payload: dict[str, Any]) -> str:
    tz_name = default_timezone()
    try:
        st = _parse_iso_to_aware(payload["start"], tz_name)
        human = st.strftime("%d/%m/%Y %H:%M")
        end_raw = payload.get("end")
        if end_raw:
            try:
                et = _parse_iso_to_aware(str(end_raw).strip(), tz_name)
                human += " – " + et.strftime("%H:%M")
            except Exception:
                pass
    except Exception:
        human = str(payload.get("start"))
    title = payload.get("title") or "Evento"
    return f"*{title}*\n🕐 {human} ({tz_name})"


def disconnect_google() -> None:
    p = token_path()
    if p.is_file():
        p.unlink()


__all__ = [
    "SCOPES",
    "oauth_configured",
    "is_authorized",
    "load_credentials",
    "token_path",
    "redirect_uri",
    "calendar_id",
    "default_timezone",
    "drive_folder_id",
    "register_oauth_state",
    "consume_oauth_state",
    "build_authorization_url",
    "finish_oauth_authorization_response",
    "create_calendar_event",
    "upload_file_to_drive",
    "extract_json_after_marker",
    "strip_marker_and_json",
    "normalize_calendar_payload",
    "format_event_for_user",
    "disconnect_google",
    "oauth_login_hint",
    "oauth_debug_log",
    "oauth_redirect_uri_warning",
]
