"""Registro de auditoría centralizado, de solo inserción y encadenado con HMAC-SHA256.

Toda escritura de eventos pasa por write() (dentro de una transacción de negocio) o por
write_standalone() (transacción propia). Cada fila guarda:
    row_hash = HMAC(AJS_AUDIT_HMAC_KEY, canon(campos + seq + prev_hash))
La fila audit_chain_head (bloqueada con FOR UPDATE) serializa las inserciones y guarda el último
eslabón, de modo que también se detecta la eliminación de las filas finales.

Alcance real de la protección (ver docs/AUDITORIA.md): la cuenta de la aplicación no tiene
UPDATE/DELETE sobre audit_log y existen disparadores que rechazan esas operaciones. Un DBA con
privilegios totales puede alterar la tabla; la cadena HMAC permite DETECTARLO siempre que la clave
HMAC no esté en su poder. No es un registro "absolutamente inalterable".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from flask import current_app, g, has_request_context

from . import db
from .security.client_info import current_client

GENESIS_HASH = "0" * 64

# Tipos de evento
LOGIN_OK = "LOGIN_EXITOSO"
LOGIN_FAILED = "LOGIN_FALLIDO"
LOGIN_BLOCKED = "LOGIN_BLOQUEADO"
LOGOUT = "LOGOUT"
SESSION_EXPIRED = "SESION_EXPIRADA"
PASSWORD_CHANGED = "CONTRASENA_CAMBIADA"
ACCESS_DENIED = "ACCESO_DENEGADO"
CSRF_FAILED = "CSRF_INVALIDO"
REPORT_CREATED = "INFORME_CREADO"
REPORT_SAVED = "INFORME_GUARDADO"
REPORT_CLOSED = "INFORME_CERRADO"
REPORT_SENT = "INFORME_ENVIADO"
REPORT_REOPENED = "INFORME_REABIERTO"
REPORT_DOWNLOADED = "INFORME_DESCARGADO"
REPORT_OP_REJECTED = "OPERACION_INFORME_RECHAZADA"
REPORTS_IMPORTED = "INFORMES_IMPORTADOS"
REOPEN_REQUESTED = "REAPERTURA_SOLICITADA"
REOPEN_REJECTED = "REAPERTURA_RECHAZADA"
ATTACHMENT_ADDED = "ADJUNTO_AGREGADO"
ATTACHMENT_REMOVED = "ADJUNTO_RETIRADO"
AUTOFILL_USED = "AUTOCOMPLETADO_INFORME"
USER_CREATED = "USUARIO_CREADO"
USER_ROLE_CHANGED = "PERMISOS_ROL_CAMBIADO"
USER_STATUS_CHANGED = "USUARIO_ESTADO_CAMBIADO"
USER_UPDATED = "USUARIO_MODIFICADO"
USER_PASSWORD_RESET = "USUARIO_CONTRASENA_RESTABLECIDA"
AUDIT_VERIFIED = "AUDITORIA_VERIFICADA"
SECURITY_ERROR = "ERROR_SEGURIDAD"

OUTCOMES = {"EXITO", "FALLO", "DENEGADO", "ERROR"}

_SECRET_HINTS = ("password", "contrasena", "contraseña", "token", "cookie", "csrf", "secret", "hash")


class AuditWriteError(RuntimeError):
    pass


def _sanitize(detail: dict | None) -> dict:
    """Elimina cualquier clave que pueda contener secretos y limita tamaños."""
    clean = {}
    for key, value in (detail or {}).items():
        k = str(key)[:64]
        if any(h in k.lower() for h in _SECRET_HINTS):
            continue
        if isinstance(value, float):
            value = str(value)  # evita diferencias de representación al verificar
        if isinstance(value, str):
            value = value[:1000]
        if isinstance(value, dict):
            value = _sanitize(value)
        clean[k] = value
    return clean


def _canonical(row: dict) -> bytes:
    fields = [
        row["seq"], row["event_uuid"], row["occurred_at"].strftime("%Y-%m-%dT%H:%M:%S.%f"),
        row["event_type"], row["outcome"], row["user_id"], row["username"], row["role_code"],
        row["session_public_id"], row["ip"], row["os_reported"], row["user_agent"],
        row["object_type"], row["object_id"], row["detail"], row["prev_hash"],
    ]
    return json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_hash(key: str, row: dict) -> str:
    return hmac.new(key.encode("utf-8"), _canonical(row), hashlib.sha256).hexdigest()


def _context_defaults() -> dict:
    if not has_request_context():
        return {"ip": None, "user_agent": None, "os_reported": None, "session_public_id": None,
                "user_id": None, "username": None, "role_code": None}
    client = current_client()
    user = g.get("user")
    sess = g.get("session_row")
    return {
        **client,
        "session_public_id": sess["public_id"] if sess else None,
        "user_id": user["id"] if user else None,
        "username": user["username"] if user else None,
        "role_code": user["role_code"] if user else None,
    }


def write(cur, event_type: str, outcome: str, *, detail: dict | None = None,
          object_type: str | None = None, object_id: str | int | None = None, **overrides) -> int:
    """Inserta un evento usando el cursor de la transacción en curso. Devuelve seq."""
    if outcome not in OUTCOMES:
        raise AuditWriteError("resultado de auditoría no válido")
    if not event_type or len(event_type) < 3:
        raise AuditWriteError("tipo de evento no válido")
    ctx = _context_defaults()
    ctx.update({k: v for k, v in overrides.items() if k in ctx})
    if ctx.get("username"):
        ctx["username"] = str(ctx["username"])[:64]

    cur.execute("SELECT last_seq, last_hash FROM audit_chain_head WHERE id = 1 FOR UPDATE")
    head = cur.fetchone()
    if head is None:
        raise AuditWriteError("cabeza de la cadena de auditoría inexistente")

    row = {
        "seq": int(head["last_seq"]) + 1,
        "event_uuid": uuid.uuid4().hex,
        "occurred_at": db.utcnow(),
        "event_type": event_type,
        "outcome": outcome,
        "object_type": object_type,
        "object_id": str(object_id) if object_id is not None else None,
        "detail": _sanitize(detail),
        "prev_hash": head["last_hash"],
        **ctx,
    }
    key = current_app.config["AJS_SETTINGS"].audit_hmac_key
    row["row_hash"] = compute_hash(key, row)
    cur.execute(
        """INSERT INTO audit_log (seq, event_uuid, occurred_at, event_type, outcome, user_id, username,
               role_code, session_public_id, ip, os_reported, user_agent, object_type, object_id,
               detail, prev_hash, row_hash)
           VALUES (%(seq)s, %(event_uuid)s, %(occurred_at)s, %(event_type)s, %(outcome)s, %(user_id)s,
               %(username)s, %(role_code)s, %(session_public_id)s, %(ip)s, %(os_reported)s, %(user_agent)s,
               %(object_type)s, %(object_id)s, %(detail_json)s, %(prev_hash)s, %(row_hash)s)""",
        {**{k: v for k, v in row.items() if k != "detail"},
         "detail_json": json.dumps(row["detail"], ensure_ascii=False, sort_keys=True)},
    )
    cur.execute("UPDATE audit_chain_head SET last_seq = %s, last_hash = %s WHERE id = 1 AND last_seq = %s",
                (row["seq"], row["row_hash"], head["last_seq"]))
    if cur.rowcount != 1:
        raise AuditWriteError("conflicto al actualizar la cadena de auditoría")
    return row["seq"]


def write_standalone(event_type: str, outcome: str, **kwargs) -> int:
    """Evento sin transacción de negocio asociada (login, denegaciones...). Falla cerrado:
    si no puede registrarse, la excepción se propaga y la operación se rechaza."""
    if g.get("in_tx"):
        raise AuditWriteError("write_standalone no puede usarse dentro de otra transacción")
    with db.transaction() as cur:
        return write(cur, event_type, outcome, **kwargs)


def verify_chain(conn, key: str, batch: int = 2000) -> dict:
    """Recalcula toda la cadena. Devuelve {'ok', 'checked', 'last_seq', 'errors': [...]}."""
    errors: list[dict] = []
    expected_prev, expected_seq, checked, last_id = GENESIS_HASH, 1, 0, 0
    with conn.cursor() as cur:
        while True:
            cur.execute(
                """SELECT id, seq, event_uuid, occurred_at, event_type, outcome, user_id, username, role_code,
                          session_public_id, ip, os_reported, user_agent, object_type, object_id, detail,
                          prev_hash, row_hash
                     FROM audit_log WHERE id > %s ORDER BY id LIMIT %s""",
                (last_id, batch),
            )
            rows = cur.fetchall()
            if not rows:
                break
            for r in rows:
                last_id = r["id"]
                checked += 1
                r = dict(r)
                r["detail"] = json.loads(r["detail"]) if isinstance(r["detail"], (str, bytes)) else r["detail"]
                if r["seq"] != expected_seq:
                    errors.append({"seq": r["seq"], "problema": f"secuencia discontinua (se esperaba {expected_seq})"})
                if r["prev_hash"] != expected_prev:
                    errors.append({"seq": r["seq"], "problema": "eslabón anterior no coincide"})
                if not hmac.compare_digest(compute_hash(key, r), r["row_hash"]):
                    errors.append({"seq": r["seq"], "problema": "contenido alterado (HMAC no coincide)"})
                expected_prev, expected_seq = r["row_hash"], r["seq"] + 1
                if len(errors) >= 50:
                    break
            if len(errors) >= 50:
                break
        cur.execute("SELECT last_seq, last_hash FROM audit_chain_head WHERE id = 1")
        head = cur.fetchone()
    conn.commit()
    if head and len(errors) < 50:
        if head["last_seq"] != expected_seq - 1 or head["last_hash"] != expected_prev:
            errors.append({"seq": head["last_seq"], "problema": "la cabeza de la cadena no coincide con el último registro (posible eliminación)"})
    return {"ok": not errors, "checked": checked, "last_seq": expected_seq - 1, "errors": errors}
