"""Operaciones administrativas (solo responsable administrativo: permiso users.manage)."""
from __future__ import annotations

from urllib.parse import quote, urlsplit

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .. import users
from ..security.passwords import POLICY_TEXT
from ..security.authz import require_perm

bp = Blueprint("admin", __name__, url_prefix="/administracion")


@bp.route("/usuarios")
@require_perm("users.manage")
def users_index():
    return render_template("admin_users.html", users=users.list_users(), roles=users.roles(), errors={}, form={},
                           policy=POLICY_TEXT)


@bp.route("/usuarios", methods=["POST"])
@require_perm("users.manage")
def users_create():
    form = request.form
    try:
        users.create_user(form.get("username"), form.get("full_name"), form.get("id_number"), form.get("password"),
                          form.get("role_id"), form.get("phone_number"))
    except users.UserError as exc:
        flash(exc.message, "error")
        return render_template("admin_users.html", users=users.list_users(), roles=users.roles(), policy=POLICY_TEXT,
                               errors=exc.field_errors, form={k: form.get(k, "") for k in
                                                              ("username", "full_name", "id_number", "phone_number", "role_id")}), 422
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


@bp.route("/usuarios/<int:user_id>/eliminar", methods=["POST"])
@require_perm("users.manage")
def users_delete(user_id: int):
    if request.form.get("confirmar") != "ELIMINAR":
        flash("Para eliminar la cuenta escriba ELIMINAR en la casilla de confirmación.", "error")
        return redirect(url_for("admin.users_index"))
    try:
        username = users.delete_user(user_id, g.user)
    except users.UserError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.users_index"))
    flash(f"Usuario {username} eliminado. Sus informes y su historial se conservan.", "success")
    return redirect(url_for("admin.users_index"))


@bp.route("/usuarios/<int:user_id>/identificacion", methods=["POST"])
@require_perm("users.manage")
def users_id_number(user_id: int):
    return _run(users.set_id_number, user_id, request.form.get("id_number", ""), g.user,
                ok="Número de identificación actualizado.")


@bp.route("/usuarios/<int:user_id>/telefono", methods=["POST"])
@require_perm("users.manage")
def users_phone_number(user_id: int):
    return _run(users.set_phone_number, user_id, request.form.get("phone_number", ""), g.user,
                ok="Número de WhatsApp actualizado.")


@bp.route("/usuarios/<int:user_id>/firma", methods=["POST"])
@require_perm("users.manage")
def users_signature(user_id: int):
    upload = request.files.get("firma")
    try:
        users.save_technician_signature(user_id, upload.read() if upload else b"")
    except users.UserError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.users_index"))
    flash("Firma del técnico guardada. Se usará en sus informes nuevos al cerrar.", "success")
    return redirect(url_for("admin.users_index"))


@bp.route("/usuarios/<int:user_id>/invitacion-whatsapp", methods=["POST"])
@require_perm("users.manage")
def users_invitation_whatsapp(user_id: int):
    base = current_app.config.get("AJS_PUBLIC_BASE_URL") or request.url_root
    base_parts = urlsplit(base)
    settings = current_app.config["AJS_SETTINGS"]
    if (not base_parts.netloc or base_parts.scheme not in ({"https"} if settings.is_production else {"http", "https"})
            or base_parts.path not in ("", "/") or base_parts.query or base_parts.fragment):
        flash("Configure AJS_PUBLIC_BASE_URL con el dominio público del sistema antes de invitar usuarios.", "error")
        return redirect(url_for("admin.users_index"))
    try:
        token, phone, name, username = users.issue_invitation(user_id, g.user)
    except users.UserError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.users_index"))
    base = base.rstrip("/")
    invite_url = base + url_for("auth.accept_invitation", token=token)
    message = (f"Hola {name}. Para activar tu primer acceso a AJ System y crear tu contraseña, "
               f"abre este enlace (válido por 72 horas): {invite_url} Usuario: {username}.")
    return redirect("https://wa.me/" + phone + "?text=" + quote(message), code=303)


@bp.route("/usuarios/<int:user_id>/contrasena", methods=["POST"])
@require_perm("users.manage")
def users_password(user_id: int):
    return _run(users.reset_password, user_id, request.form.get("password", ""), g.user,
                ok="Contraseña restablecida. El usuario deberá cambiarla al iniciar sesión.")


@bp.route("/roles/<code>/nombre", methods=["POST"])
@require_perm("users.manage")
def role_name(code: str):
    return _run(users.set_role_display_name, code[:32], request.form.get("display_name", ""), ok="Nombre visible del rol actualizado.")
