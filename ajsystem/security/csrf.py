"""Protección CSRF.

- Usuarios autenticados: token sincronizado guardado en la fila de sesión (servidor).
- Formulario de inicio de sesión (anónimo): doble envío firmado; la cookie lleva un valor
  aleatorio y el formulario lleva HMAC(secret_key, valor).
- Además, si el navegador envía Origin, debe coincidir con el host de la solicitud.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from flask import current_app, g, request

PRE_COOKIE = "ajs_pre"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _settings():
    return current_app.config["AJS_SETTINGS"]


def _pre_cookie_name() -> str:
    return f"__Host-{PRE_COOKIE}" if _settings().cookie_secure else PRE_COOKIE


def _sign(value: str) -> str:
    return hmac.new(_settings().secret_key.encode(), b"login-csrf:" + value.encode(), hashlib.sha256).hexdigest()


def token_for_template() -> str:
    """Token a incluir en formularios (campo oculto csrf_token)."""
    if g.get("session_row"):
        return g.session_row["csrf_token"]
    raw = request.cookies.get(_pre_cookie_name())
    if not raw or len(raw) != 64:
        raw = g.get("new_pre_token") or secrets.token_hex(32)
        g.new_pre_token = raw
    return _sign(raw)


def attach_pre_cookie(response):
    raw = g.get("new_pre_token")
    if raw:
        response.set_cookie(_pre_cookie_name(), raw, secure=_settings().cookie_secure,
                            httponly=True, samesite="Strict", path="/", max_age=2 * 3600)
    return response


def origin_ok() -> bool:
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return origin is None  # "null" (contextos opacos) se rechaza
    return urlsplit(origin).netloc == request.host


def is_valid() -> bool:
    if request.method in SAFE_METHODS:
        return True
    if not origin_ok():
        return False
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    if g.get("session_row"):
        return bool(sent) and hmac.compare_digest(sent, g.session_row["csrf_token"])
    raw = request.cookies.get(_pre_cookie_name())
    if not raw or not sent:
        return False
    return hmac.compare_digest(sent, _sign(raw))
