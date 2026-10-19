"""Inicio y cierre de sesión, cambio de contraseña."""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .. import audit, db, users
from ..security import passwords, sessions
from ..security.authz import login_required
from ..security.client_info import current_client

bp = Blueprint("auth", __name__)
GENERIC_FAIL = "Usuario o contraseña incorrectos."


def _safe_next(target: str | None) -> str:
    if (target and target.startswith("/") and not target.startswith("//") and "\\" not in target
            and not any(ord(c) < 32 for c in target) and not urlsplit(target).netloc):
        return target
    return url_for("home.index")


def _too_many_attempts(username: str, ip: str) -> bool:
    s = current_app.config["AJS_SETTINGS"]
    since = db.utcnow() - dt.timedelta(minutes=s.login_window_minutes)
    by_user = db.query_one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event_type = %s AND username = %s AND occurred_at >= %s",
        (audit.LOGIN_FAILED, username, since))["n"]
    by_ip = db.query_one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event_type = %s AND ip = %s AND occurred_at >= %s",
        (audit.LOGIN_FAILED, ip, since))["n"]
    return by_user >= s.login_max_failures_user or by_ip >= s.login_max_failures_ip


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("home.index"))
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", ""))

    username = users.normalize_username(request.form.get("username", ""))
    password = request.form.get("password", "")
    next_url = request.form.get("next", "")
    client = current_client()

    if not username or not password or len(password) > passwords.MAX_LENGTH:
        flash(GENERIC_FAIL, "error")
        return render_template("login.html", next=next_url, username=username), 400

    # Se admite el nombre de usuario o el número de identificación (ambos son únicos entre sí).
    row = db.query_one(
        """SELECT u.id, u.username, u.full_name, u.password_hash, u.is_active, r.code AS role_code
             FROM users u JOIN roles r ON r.id = u.role_id
            WHERE u.username = %s OR u.id_number = %s
            ORDER BY u.username = %s DESC LIMIT 1""",
        (username, users.normalize_id_number(username) or None, username))
    typed = username  # lo que escribió la persona (no se revela el usuario asociado a una identificación)
    if row:
        username = row["username"]  # el registro y el límite de intentos usan el usuario real
    if _too_many_attempts(username, client["ip"]):
        audit.write_standalone(audit.LOGIN_BLOCKED, "DENEGADO", username=username,
                               detail={"motivo": "límite de intentos fallidos alcanzado"})
        flash("Demasiados intentos fallidos. Espere unos minutos antes de volver a intentarlo.", "error")
        return render_template("login.html", next=next_url, username=typed), 429

    ok = passwords.verify_password(row["password_hash"] if row else None, password)
    if not ok or not row["is_active"]:
        reason = "usuario inexistente" if not row else ("cuenta inactiva" if ok else "contraseña incorrecta")
        audit.write_standalone(audit.LOGIN_FAILED, "FALLO", username=username,
                               user_id=row["id"] if row else None, role_code=row["role_code"] if row else None,
                               detail={"motivo": reason})
        flash(GENERIC_FAIL, "error")
        return render_template("login.html", next=next_url, username=typed), 401

    previous_token = request.cookies.get(current_app.config["AJS_SETTINGS"].cookie_name)
    with db.transaction() as cur:
        if previous_token:  # cualquier sesión previa presentada por el navegador se revoca
            cur.execute("UPDATE sessions SET ended_at = %s, end_reason = 'REVOCADA' WHERE token_hash = %s AND ended_at IS NULL",
                        (db.utcnow(), sessions._hash_token(previous_token)))
        token, sess = sessions.create_session(cur, row)
        new_hash = passwords.hash_password(password) if passwords.needs_rehash(row["password_hash"]) else None
        if new_hash:
            cur.execute("UPDATE users SET last_login_at = %s, password_hash = %s WHERE id = %s",
                        (sess["created_at"], new_hash, row["id"]))
        else:
            cur.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (sess["created_at"], row["id"]))
        audit.write(cur, audit.LOGIN_OK, "EXITO", user_id=row["id"], username=row["username"],
                    role_code=row["role_code"], session_public_id=sess["public_id"],
                    object_type="sesion", object_id=sess["public_id"], detail={"metodo": "contraseña"})
    response = redirect(_safe_next(next_url))
    sessions.set_cookie(response, token)
    return response


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    sessions.end_session(g.session_row, "LOGOUT", audit.LOGOUT, {"origen": "usuario"})
    response = redirect(url_for("auth.login"))
    sessions.clear_cookie(response)
    flash("Sesión cerrada correctamente.", "success")
    return response


@bp.route("/cuenta/contrasena", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        new, confirm = request.form.get("new", ""), request.form.get("confirm", "")
        try:
            if new != confirm:
                raise users.UserError("La confirmación no coincide.", {"confirm": "No coincide con la nueva contraseña."})
            users.change_own_password(g.user, request.form.get("current", ""), new, g.session_row["id"])
        except users.UserError as exc:
            flash(exc.message, "error")
            return render_template("change_password.html", errors=exc.field_errors), 422
        flash("Contraseña actualizada.", "success")
        return redirect(url_for("home.index"))
    return render_template("change_password.html", errors={})
