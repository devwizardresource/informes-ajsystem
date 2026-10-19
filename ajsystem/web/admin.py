"""Operaciones administrativas (solo responsable administrativo: permiso users.manage)."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import users
from ..security.authz import require_perm

bp = Blueprint("admin", __name__, url_prefix="/administracion")


@bp.route("/usuarios")
@require_perm("users.manage")
def users_index():
    return render_template("admin_users.html", users=users.list_users(), roles=users.roles(), errors={}, form={})


@bp.route("/usuarios", methods=["POST"])
@require_perm("users.manage")
def users_create():
    form = request.form
    try:
        users.create_user(form.get("username"), form.get("full_name"), form.get("email"), form.get("password"),
                          form.get("role_id"))
    except users.UserError as exc:
        flash(exc.message, "error")
        return render_template("admin_users.html", users=users.list_users(), roles=users.roles(),
                               errors=exc.field_errors, form={k: form.get(k, "") for k in ("username", "full_name", "email", "role_id")}), 422
    flash("Usuario creado. Deberá cambiar la contraseña en su primer acceso.", "success")
    return redirect(url_for("admin.users_index"))


def _run(fn, *args, ok: str):
    try:
        fn(*args)
        flash(ok, "success")
    except users.UserError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.users_index"))


@bp.route("/usuarios/<int:user_id>/rol", methods=["POST"])
@require_perm("users.manage")
def users_role(user_id: int):
    return _run(users.change_role, user_id, request.form.get("role_id"), g.user, ok="Rol actualizado; sesiones del usuario revocadas.")


@bp.route("/usuarios/<int:user_id>/estado", methods=["POST"])
@require_perm("users.manage")
def users_status(user_id: int):
    return _run(users.set_active, user_id, request.form.get("activo") == "1", g.user, ok="Estado de la cuenta actualizado.")


@bp.route("/usuarios/<int:user_id>/contrasena", methods=["POST"])
@require_perm("users.manage")
def users_password(user_id: int):
    return _run(users.reset_password, user_id, request.form.get("password", ""), g.user,
                ok="Contraseña restablecida. El usuario deberá cambiarla al iniciar sesión.")


@bp.route("/roles/<code>/nombre", methods=["POST"])
@require_perm("users.manage")
def role_name(code: str):
    return _run(users.set_role_display_name, code[:32], request.form.get("display_name", ""), ok="Nombre visible del rol actualizado.")
