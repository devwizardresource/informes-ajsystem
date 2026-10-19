"""Comandos de operación: flask --app wsgi <comando>.

Las acciones administrativas ejecutadas por CLI se registran en auditoría con el usuario
'cli:<usuario del sistema operativo>'. Las contraseñas se piden de forma interactiva y nunca
se aceptan como argumento (quedarían en el historial del shell).
"""
from __future__ import annotations

import getpass
import os
import sys

import click
from flask import current_app

from . import audit, db, dbadmin, users


def _cli_actor() -> dict:
    return {"username": f"cli:{getpass.getuser()}"[:64], "ip": "local", "os_reported": sys.platform}


def register_cli(app) -> None:
    @app.cli.command("db-bootstrap")
    @click.option("--root-user", default="root", show_default=True)
    @click.option("--account-host", default="%", show_default=True,
                  help="Host de las cuentas MySQL (en producción, restrínjalo: p. ej. localhost).")
    def db_bootstrap(root_user, account_host):
        """Crea base, cuentas, aplica migraciones y privilegios (requiere cuenta DBA)."""
        s = current_app.config["AJS_SETTINGS"]
        root_pw = os.environ.get("AJS_DB_ROOT_PASSWORD") or getpass.getpass(f"Contraseña de {root_user}: ")
        dbadmin.bootstrap(s, root_user, root_pw, account_host, log=click.echo)
        applied = dbadmin.migrate(s, user=root_user, password=root_pw, log=click.echo)
        click.echo(f"Migraciones aplicadas: {applied or 'ninguna (al día)'}")
        dbadmin.apply_grants(s, root_user, root_pw, account_host, log=click.echo)

    @app.cli.command("db-migrate")
    @click.option("--user", "user", default="root", show_default=True, help="Cuenta con privilegios DDL y TRIGGER")
    def db_migrate(user):
        """Aplica migraciones pendientes. Haga un respaldo antes (scripts/backup.py)."""
        s = current_app.config["AJS_SETTINGS"]
        known = {"root": os.environ.get("AJS_DB_ROOT_PASSWORD"),
                 s.db_admin_user: s.db_admin_password, s.db_user: s.db_password}
        # En alojamientos sin cuenta DBA (Hostinger compartido) se migra con la cuenta de hPanel
        # sin pedir la contraseña: ya está en el entorno del proceso.
        pw = known.get(user) or getpass.getpass(f"Contraseña de {user}: ")
        applied = dbadmin.migrate(s, user=user, password=pw, log=click.echo)
        click.echo(f"Migraciones aplicadas: {applied or 'ninguna (al día)'}")

    @app.cli.command("create-user")
    @click.option("--username", required=True)
    @click.option("--full-name", required=True)
    @click.option("--role", type=click.Choice(["RESPONSABLE", "JEFE", "TECNICO"]), required=True)
    @click.option("--id-number", default="", help="Número de identificación (también sirve para iniciar sesión)")
    def create_user(username, full_name, role, id_number):
        """Crea un usuario (pide la contraseña temporal de forma interactiva)."""
        password = os.environ.get("AJS_NEW_USER_PASSWORD") or click.prompt(
            "Contraseña temporal", hide_input=True, confirmation_prompt=True)
        role_row = db.query_one("SELECT id FROM roles WHERE code = %s", (role,))
        try:
            uid = users.create_user(username, full_name, id_number, password, role_row["id"],
                                    actor_note={"origen": "cli"}, audit_overrides=_cli_actor())
        except users.UserError as exc:
            raise click.ClickException(f"{exc.message} {exc.field_errors}")
        click.echo(f"Usuario creado con id {uid}. Deberá cambiar la contraseña en su primer acceso.")

    @app.cli.command("ensure-initial-user")
    def ensure_initial_user():
        """Crea el primer RESPONSABLE si aún no existe ninguno (despliegue sin consola).

        Lee AJS_INITIAL_ADMIN_USER, AJS_INITIAL_ADMIN_NAME y AJS_INITIAL_ADMIN_PASSWORD. La
        contraseña es temporal: el usuario debe cambiarla en su primer acceso. Si ya hay un
        responsable no hace nada, así que puede ejecutarse en cada arranque.
        """
        if db.query_one("SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'RESPONSABLE' LIMIT 1"):
            click.echo("Ya existe un responsable; no se crea ningún usuario.")
            return
        username = os.environ.get("AJS_INITIAL_ADMIN_USER", "").strip()
        password = os.environ.get("AJS_INITIAL_ADMIN_PASSWORD", "")
        if not username or not password:
            raise click.ClickException("Defina AJS_INITIAL_ADMIN_USER y AJS_INITIAL_ADMIN_PASSWORD.")
        full_name = os.environ.get("AJS_INITIAL_ADMIN_NAME", "").strip() or "Responsable"
        role_row = db.query_one("SELECT id FROM roles WHERE code = 'RESPONSABLE'")
        try:
            uid = users.create_user(username, full_name, os.environ.get("AJS_INITIAL_ADMIN_ID", ""), password,
                                    role_row["id"],
                                    actor_note={"origen": "arranque"}, audit_overrides=_cli_actor())
        except users.UserError as exc:
            raise click.ClickException(f"{exc.message} {exc.field_errors}")
        click.echo(f"Responsable inicial creado con id {uid}. Deberá cambiar la contraseña en su primer acceso.")

    @app.cli.command("set-role-name")
    @click.argument("role", type=click.Choice(["RESPONSABLE", "JEFE", "TECNICO"]))
    @click.argument("display_name")
    def set_role_name(role, display_name):
        """Cambia el nombre visible de un rol (p. ej. el del responsable administrativo)."""
        try:
            users.set_role_display_name(role, display_name, audit_overrides=_cli_actor())
        except users.UserError as exc:
            raise click.ClickException(exc.message)
        click.echo("Nombre visible actualizado.")

    @app.cli.command("verify-audit")
    def verify_audit():
        """Verifica la cadena HMAC del registro de auditoría. Código de salida 1 si hay incidencias."""
        result = audit.verify_chain(db.get_conn(), current_app.config["AJS_SETTINGS"].audit_hmac_key)
        audit.write_standalone(audit.AUDIT_VERIFIED, "EXITO" if result["ok"] else "FALLO",
                               detail={"registros_verificados": result["checked"], "integro": result["ok"],
                                       "incidencias": len(result["errors"]), "origen": "cli"}, **_cli_actor())
        click.echo(f"Registros verificados: {result['checked']}")
        if result["ok"]:
            click.echo("Cadena íntegra.")
        else:
            for err in result["errors"]:
                click.echo(f"  seq {err['seq']}: {err['problema']}")
            sys.exit(1)
