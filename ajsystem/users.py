"""Gestión de usuarios (operaciones administrativas auditadas)."""
from __future__ import annotations

import re

from . import audit, db
from .security import passwords

# Nombre de usuario: puede ser un alias, el nombre completo o el número de identificación.
USERNAME_RE = re.compile(r"^(?=.{3,64}$)[a-z0-9áéíóúüñ._-]+(?: [a-z0-9áéíóúüñ._-]+)*$")
ID_NUMBER_RE = re.compile(r"^[A-Z0-9-]{4,20}$")


class UserError(Exception):
    def __init__(self, message: str, field_errors: dict | None = None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}


def normalize_username(value: str) -> str:
    return " ".join((value or "").split()).lower()[:64]


def normalize_id_number(value: str) -> str:
    """Número de identificación sin puntos ni espacios (1.234.567 → 1234567)."""
    return "".join(c for c in (value or "") if c not in ". ").upper()[:20]


def roles() -> list[dict]:
    return db.query_all("SELECT id, code, display_name FROM roles ORDER BY id")


def list_users() -> list[dict]:
    return db.query_all(
        """SELECT u.id, u.username, u.full_name, u.id_number, u.is_active, u.must_change_password, u.created_at,
                  u.last_login_at, r.code AS role_code, r.display_name AS role_name
             FROM users u JOIN roles r ON r.id = u.role_id ORDER BY r.id, u.full_name""")


def technicians_and_owners() -> list[dict]:
    return db.query_all("SELECT id, full_name, username FROM users ORDER BY full_name")


def _validate_new(username: str, full_name: str, id_number: str, password: str, role_id) -> dict:
    errors = {}
    if not USERNAME_RE.match(username):
        errors["username"] = ("Entre 3 y 64 caracteres: letras, números, espacios, punto, guion o guion bajo "
                              "(puede usar el nombre completo o el número de identificación).")
    if not (2 <= len(full_name) <= 120):
        errors["full_name"] = "Nombre obligatorio (2 a 120 caracteres)."
    if id_number and not ID_NUMBER_RE.match(id_number):
        errors["id_number"] = "Número de identificación no válido (4 a 20 letras, números o guion)."
    pw_errors = passwords.validate_policy(password, username)
    if pw_errors:
        errors["password"] = " ".join(pw_errors)
    if not db.query_one("SELECT id FROM roles WHERE id = %s", (role_id,)):
        errors["role_id"] = "Rol no válido."
    return errors


def _identity_taken(cur, username: str | None, id_number: str | None, exclude_id: int | None = None) -> dict:
    """El usuario y el número de identificación sirven para iniciar sesión: no pueden coincidir con los de otro."""
    errors = {}
    keys = [k for k in (username, id_number.lower() if id_number else None) if k]
    placeholders = ",".join(["%s"] * len(keys))
    cur.execute(f"""SELECT id, username, id_number FROM users
                     WHERE (LOWER(username) IN ({placeholders}) OR LOWER(id_number) IN ({placeholders}))
                       AND id <> %s FOR UPDATE""", (*keys, *keys, exclude_id or 0))
    for row in cur.fetchall():
        hits = {row["username"].lower(), (row["id_number"] or "").lower()}
        if username and username in hits:
            errors["username"] = "Nombre de usuario en uso."
        if id_number and id_number.lower() in hits:
            errors["id_number"] = "Número de identificación ya registrado."
    return errors


def create_user(username, full_name, id_number, password, role_id, *, actor_note: dict | None = None,
                must_change: bool = True, audit_overrides: dict | None = None) -> int:
    username = normalize_username(username)
    full_name = " ".join((full_name or "").split())
    id_number = normalize_id_number(id_number) or None
    try:
        role_id = int(role_id)
    except (TypeError, ValueError):
        role_id = -1
    errors = _validate_new(username, full_name, id_number or "", password or "", role_id)
    if errors:
        raise UserError("Revise los datos del usuario.", errors)
    pw_hash = passwords.hash_password(password)
    with db.transaction() as cur:
        taken = _identity_taken(cur, username, id_number)
        if taken:
            raise UserError("Ya existe un usuario con ese nombre o número de identificación.", taken)
        now = db.utcnow()
        cur.execute(
            """INSERT INTO users (username, full_name, id_number, password_hash, role_id, is_active,
                                  must_change_password, created_at, updated_at, password_changed_at)
               VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s)""",
            (username, full_name, id_number, pw_hash, role_id, 1 if must_change else 0, now, now, now))
        new_id = cur.lastrowid
        cur.execute("SELECT code FROM roles WHERE id = %s", (role_id,))
        role_code = cur.fetchone()["code"]
        audit.write(cur, audit.USER_CREATED, "EXITO", object_type="usuario", object_id=username,
                    detail={"usuario_id": new_id, "rol": role_code, **(actor_note or {})}, **(audit_overrides or {}))
    return new_id


def _lock_user(cur, user_id: int) -> dict:
    cur.execute("""SELECT u.id, u.username, u.role_id, u.is_active, r.code AS role_code
                     FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s FOR UPDATE""", (user_id,))
    row = cur.fetchone()
    if not row:
        raise UserError("Usuario no encontrado.")
    return row


def _active_responsables(cur) -> int:
    cur.execute("""SELECT COUNT(*) AS n FROM users u JOIN roles r ON r.id = u.role_id
                    WHERE r.code = 'RESPONSABLE' AND u.is_active = 1 FOR UPDATE""")
    return cur.fetchone()["n"]


def change_role(user_id: int, role_id, actor: dict) -> None:
    with db.transaction() as cur:
        target = _lock_user(cur, user_id)
        cur.execute("SELECT id, code FROM roles WHERE id = %s", (role_id,))
        role = cur.fetchone()
        if not role:
            raise UserError("Rol no válido.")
        if role["id"] == target["role_id"]:
            return
        if target["role_code"] == "RESPONSABLE" and target["is_active"] and _active_responsables(cur) <= 1:
            raise UserError("No se puede retirar el rol al último responsable administrativo activo.")
        cur.execute("UPDATE users SET role_id = %s, updated_at = %s WHERE id = %s", (role["id"], db.utcnow(), user_id))
        # Las sesiones abiertas del usuario se revocan para que el nuevo rol se aplique de inmediato.
        cur.execute("UPDATE sessions SET ended_at = %s, end_reason = 'REVOCADA' WHERE user_id = %s AND ended_at IS NULL",
                    (db.utcnow(), user_id))
        audit.write(cur, audit.USER_ROLE_CHANGED, "EXITO", object_type="usuario", object_id=target["username"],
                    detail={"usuario_id": user_id, "rol_anterior": target["role_code"], "rol_nuevo": role["code"],
                            "sesiones_revocadas": cur.rowcount})


def set_active(user_id: int, active: bool, actor: dict) -> None:
    with db.transaction() as cur:
        target = _lock_user(cur, user_id)
        if bool(target["is_active"]) == active:
            return
        if target["id"] == actor["id"] and not active:
            raise UserError("No puede desactivar su propia cuenta.")
        if not active and target["role_code"] == "RESPONSABLE" and _active_responsables(cur) <= 1:
            raise UserError("No se puede desactivar al último responsable administrativo activo.")
        now = db.utcnow()
        cur.execute("UPDATE users SET is_active = %s, updated_at = %s WHERE id = %s", (1 if active else 0, now, user_id))
        revoked = 0
        if not active:
            cur.execute("UPDATE sessions SET ended_at = %s, end_reason = 'REVOCADA' WHERE user_id = %s AND ended_at IS NULL",
                        (now, user_id))
            revoked = cur.rowcount
        audit.write(cur, audit.USER_STATUS_CHANGED, "EXITO", object_type="usuario", object_id=target["username"],
                    detail={"usuario_id": user_id, "activo": active, "sesiones_revocadas": revoked})


def set_id_number(user_id: int, id_number: str, actor: dict) -> None:
    id_number = normalize_id_number(id_number) or None
    if id_number and not ID_NUMBER_RE.match(id_number):
        raise UserError("Número de identificación no válido (4 a 20 letras, números o guion).")
    with db.transaction() as cur:
        target = _lock_user(cur, user_id)
        cur.execute("SELECT id_number FROM users WHERE id = %s", (user_id,))
        previous = cur.fetchone()["id_number"]
        if previous == id_number:
            return
        if id_number:
            taken = _identity_taken(cur, None, id_number, exclude_id=user_id)
            if taken:
                raise UserError("Ese número de identificación ya está registrado o es el usuario de otra persona.")
        cur.execute("UPDATE users SET id_number = %s, updated_at = %s WHERE id = %s", (id_number, db.utcnow(), user_id))
        audit.write(cur, audit.USER_UPDATED, "EXITO", object_type="usuario", object_id=target["username"],
                    detail={"usuario_id": user_id, "cambio": "número de identificación",
                            "anterior": previous, "nuevo": id_number})


def reset_password(user_id: int, new_password: str, actor: dict) -> None:
    with db.transaction() as cur:
        target = _lock_user(cur, user_id)
        errors = passwords.validate_policy(new_password, target["username"])
        if errors:
            raise UserError(" ".join(errors), {"password": " ".join(errors)})
        now = db.utcnow()
        cur.execute("""UPDATE users SET password_hash = %s, must_change_password = 1, password_changed_at = %s,
                              updated_at = %s WHERE id = %s""",
                    (passwords.hash_password(new_password), now, now, user_id))
        cur.execute("UPDATE sessions SET ended_at = %s, end_reason = 'REVOCADA' WHERE user_id = %s AND ended_at IS NULL",
                    (now, user_id))
        audit.write(cur, audit.USER_PASSWORD_RESET, "EXITO", object_type="usuario", object_id=target["username"],
                    detail={"usuario_id": user_id, "sesiones_revocadas": cur.rowcount})


def change_own_password(user: dict, current: str, new: str, session_id: int) -> None:
    row = db.query_one("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
    if not row or not passwords.verify_password(row["password_hash"], current or ""):
        raise UserError("La contraseña actual no es correcta.", {"current": "Contraseña actual incorrecta."})
    errors = passwords.validate_policy(new or "", user["username"])
    if current == new:
        errors.append("La nueva contraseña debe ser distinta de la actual.")
    if errors:
        raise UserError(" ".join(errors), {"new": " ".join(errors)})
    with db.transaction() as cur:
        now = db.utcnow()
        cur.execute("""UPDATE users SET password_hash = %s, must_change_password = 0, password_changed_at = %s,
                              updated_at = %s WHERE id = %s""", (passwords.hash_password(new), now, now, user["id"]))
        # Cierra las demás sesiones abiertas del usuario.
        cur.execute("""UPDATE sessions SET ended_at = %s, end_reason = 'REVOCADA'
                        WHERE user_id = %s AND ended_at IS NULL AND id <> %s""", (now, user["id"], session_id))
        audit.write(cur, audit.PASSWORD_CHANGED, "EXITO", object_type="usuario", object_id=user["username"],
                    detail={"otras_sesiones_revocadas": cur.rowcount})


def set_role_display_name(role_code: str, display_name: str, audit_overrides: dict | None = None) -> None:
    display_name = (display_name or "").strip()
    if not (2 <= len(display_name) <= 80):
        raise UserError("El nombre visible debe tener entre 2 y 80 caracteres.")
    with db.transaction() as cur:
        cur.execute("SELECT id, display_name FROM roles WHERE code = %s FOR UPDATE", (role_code,))
        role = cur.fetchone()
        if not role:
            raise UserError("Rol no encontrado.")
        cur.execute("UPDATE roles SET display_name = %s WHERE id = %s", (display_name, role["id"]))
        audit.write(cur, audit.USER_ROLE_CHANGED, "EXITO", object_type="rol", object_id=role_code,
                    detail={"cambio": "nombre visible", "anterior": role["display_name"], "nuevo": display_name},
                    **(audit_overrides or {}))
