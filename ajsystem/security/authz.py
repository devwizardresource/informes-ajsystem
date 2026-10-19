"""Autorización en el servidor: por rol (permisos) y por recurso (ver reports.service).

La interfaz oculta lo que el usuario no puede hacer, pero la decisión real se toma aquí.
"""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, g, redirect, request, url_for

from .. import audit


def has_perm(code: str) -> bool:
    user = g.get("user")
    return bool(user) and code in user["permissions"]


def deny(reason: str, *, object_type: str | None = None, object_id=None, status: int = 403):
    """Registra el intento no autorizado y corta la solicitud."""
    audit.write_standalone(
        audit.ACCESS_DENIED, "DENEGADO",
        detail={"motivo": reason, "ruta": request.path[:200], "metodo": request.method},
        object_type=object_type, object_id=object_id,
    )
    abort(status)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            if g.get("session_expired"):
                flash("Su sesión caducó. Inicie sesión de nuevo.", "info")
            if request.method == "GET":
                return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))
            abort(401)
        return view(*args, **kwargs)
    return wrapper


def require_perm(*codes: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            missing = [c for c in codes if not has_perm(c)]
            if missing:
                deny(f"permiso requerido: {', '.join(missing)}")
            return view(*args, **kwargs)
        return wrapper
    return decorator
