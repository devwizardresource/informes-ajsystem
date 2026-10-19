"""Sesiones del lado del servidor persistidas en MySQL.

- La cookie solo contiene un token aleatorio de 256 bits; en la base se guarda su SHA-256.
- Cada inicio de sesión crea un token nuevo (protección frente a fijación de sesión).
- Caducidad por inactividad y caducidad absoluta; ambas se comprueban en el servidor.
- Las sesiones no se borran: se marcan como terminadas (LOGOUT, EXPIRADA o REVOCADA).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid

from flask import current_app, g, request

from .. import audit, db
from .client_info import current_client

_TOUCH_INTERVAL = dt.timedelta(seconds=60)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _settings():
    return current_app.config["AJS_SETTINGS"]


def load_permissions(role_id: int) -> frozenset[str]:
    rows = db.query_all(
        """SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = %s""", (role_id,))
    return frozenset(r["code"] for r in rows)


def load_current_session() -> None:
    """Carga g.user / g.session_row a partir de la cookie. Se ejecuta en cada solicitud."""
    g.user = None
    g.session_row = None
    g.session_expired = False
    token = request.cookies.get(_settings().cookie_name)
    if not token or len(token) > 128:
        return
    row = db.query_one(
        """SELECT s.id, s.public_id, s.user_id, s.csrf_token, s.created_at, s.last_seen_at,
                  s.absolute_expires_at, u.username, u.full_name, u.is_active, u.must_change_password,
                  u.role_id, r.code AS role_code, r.display_name AS role_name
             FROM sessions s
             JOIN users u ON u.id = s.user_id
             JOIN roles r ON r.id = u.role_id
            WHERE s.token_hash = %s AND s.ended_at IS NULL""",
        (_hash_token(token),),
    )
    if row is None:
        return
    now = db.utcnow()
    idle_limit = row["last_seen_at"] + dt.timedelta(minutes=_settings().session_idle_minutes)
    if not row["is_active"]:
        end_session(row, "REVOCADA", audit.SESSION_EXPIRED, {"motivo": "cuenta desactivada"})
        g.session_expired = True
        return
    if now >= row["absolute_expires_at"] or now >= idle_limit:
        reason = "caducidad absoluta" if now >= row["absolute_expires_at"] else "inactividad"
        end_session(row, "EXPIRADA", audit.SESSION_EXPIRED, {"motivo": reason})
        g.session_expired = True
        return
    if now - row["last_seen_at"] >= _TOUCH_INTERVAL:
        with db.transaction() as cur:
            cur.execute("UPDATE sessions SET last_seen_at = %s WHERE id = %s AND ended_at IS NULL", (now, row["id"]))
    g.session_row = row
    g.user = {
        "id": row["user_id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "role_id": row["role_id"],
        "role_code": row["role_code"],
        "role_name": row["role_name"],
        "must_change_password": bool(row["must_change_password"]),
        "permissions": load_permissions(row["role_id"]),
    }


def end_session(row: dict, reason: str, event_type: str, detail: dict | None = None) -> bool:
    """Termina una sesión y registra el evento en la misma transacción."""
    with db.transaction() as cur:
        cur.execute("UPDATE sessions SET ended_at = %s, end_reason = %s WHERE id = %s AND ended_at IS NULL",
                    (db.utcnow(), reason, row["id"]))
        if cur.rowcount != 1:
            return False
        cur.execute("SELECT r.code FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s", (row["user_id"],))
        role = cur.fetchone()
        audit.write(cur, event_type, "EXITO", detail=detail or {}, object_type="sesion",
                    object_id=row["public_id"], user_id=row["user_id"], username=row["username"],
                    role_code=role["code"] if role else None, session_public_id=row["public_id"])
    return True


def create_session(cur, user: dict) -> tuple[str, dict]:
    """Crea la sesión dentro de la transacción de login. Devuelve (token_para_cookie, fila)."""
    s = _settings()
    now = db.utcnow()
    token = secrets.token_urlsafe(32)
    client = current_client()
    row = {
        "public_id": uuid.uuid4().hex,
        "token_hash": _hash_token(token),
        "user_id": user["id"],
        "csrf_token": secrets.token_hex(32),
        "created_at": now,
        "last_seen_at": now,
        "absolute_expires_at": now + dt.timedelta(hours=s.session_absolute_hours),
        "ip": client["ip"],
        "user_agent": client["user_agent"],
    }
    cur.execute(
        """INSERT INTO sessions (public_id, token_hash, user_id, csrf_token, created_at, last_seen_at,
                                 absolute_expires_at, ip, user_agent)
           VALUES (%(public_id)s, %(token_hash)s, %(user_id)s, %(csrf_token)s, %(created_at)s,
                   %(last_seen_at)s, %(absolute_expires_at)s, %(ip)s, %(user_agent)s)""",
        row,
    )
    return token, row


def set_cookie(response, token: str) -> None:
    s = _settings()
    response.set_cookie(
        s.cookie_name, token,
        max_age=s.session_absolute_hours * 3600,
        secure=s.cookie_secure, httponly=True, samesite="Lax", path="/",
    )


def clear_cookie(response) -> None:
    s = _settings()
    response.delete_cookie(s.cookie_name, path="/", secure=s.cookie_secure, httponly=True, samesite="Lax")
