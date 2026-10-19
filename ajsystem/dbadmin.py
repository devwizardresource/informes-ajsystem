"""Operaciones de administración de la base de datos (no las usa la aplicación web).

- bootstrap(): crea la base, la cuenta propietaria y la cuenta de aplicación (requiere root/DBA).
- migrate(): aplica migraciones con la cuenta propietaria.
- apply_grants(): aplica privilegios mínimos a la cuenta de aplicación.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import db
from .migrations import apply_migrations, default_collation, split_statements

GRANTS_FILE = Path(__file__).resolve().parent.parent / "sql" / "grants_app.sql"
_IDENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_HOST = re.compile(r"^[A-Za-z0-9_.%:-]{1,255}$")


def _check_ident(value: str, what: str) -> str:
    if not _IDENT.match(value):
        raise ValueError(f"{what} contiene caracteres no permitidos")
    return value


def _check_host(value: str) -> str:
    if not _HOST.match(value):
        raise ValueError("host de cuenta MySQL no válido")
    return value


def bootstrap(settings, root_user: str, root_password: str, account_host: str = "%", log=print) -> None:
    dbname = _check_ident(settings.db_name, "AJS_DB_NAME")
    owner = _check_ident(settings.db_admin_user, "AJS_DB_ADMIN_USER")
    app = _check_ident(settings.db_user, "AJS_DB_USER")
    host = _check_host(account_host)
    conn = db.connect(settings, user=root_user, password=root_password, database=None, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{dbname}` CHARACTER SET utf8mb4 "
                        f"COLLATE {default_collation(conn)}")
            for user, pwd in ((owner, settings.db_admin_password), (app, settings.db_password)):
                if len(pwd) < 16:
                    raise ValueError(f"La contraseña de la cuenta {user} debe tener al menos 16 caracteres")
                # Contraseñas como parámetro; nunca se imprimen.
                h = host.replace("%", "%%")  # escapa el comodín para el formateo de PyMySQL
                cur.execute(f"CREATE USER IF NOT EXISTS '{user}'@'{h}' IDENTIFIED BY %s", (pwd,))
                cur.execute(f"ALTER USER '{user}'@'{h}' IDENTIFIED BY %s", (pwd,))
            cur.execute(f"GRANT ALL PRIVILEGES ON `{dbname}`.* TO '{owner}'@'{host}'")
        log(f"Base '{dbname}' y cuentas '{owner}' (propietaria) y '{app}' (aplicación) preparadas.")
    finally:
        conn.close()


def migrate(settings, user: str | None = None, password: str | None = None, log=print) -> list[str]:
    conn = db.connect(settings, user=user or settings.db_admin_user,
                      password=password if password is not None else settings.db_admin_password)
    try:
        return apply_migrations(conn, log=log)
    finally:
        conn.close()


def apply_grants(settings, root_user: str, root_password: str, account_host: str = "%", log=print) -> None:
    sql = GRANTS_FILE.read_text(encoding="utf-8").format(
        db=_check_ident(settings.db_name, "AJS_DB_NAME"),
        app_user=_check_ident(settings.db_user, "AJS_DB_USER"),
        app_host=_check_host(account_host),
    )
    conn = db.connect(settings, user=root_user, password=root_password, database=None, autocommit=True)
    try:
        with conn.cursor() as cur:
            for stmt in split_statements(sql):
                cur.execute(stmt)
        log(f"Privilegios mínimos aplicados a '{settings.db_user}'.")
    finally:
        conn.close()
