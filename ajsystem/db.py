"""Acceso a MySQL: conexión por solicitud, transacciones explícitas y consultas parametrizadas.

Regla del proyecto: ninguna consulta concatena valores del usuario; siempre se usan
parámetros (%s) de PyMySQL. Los nombres de columna dinámicos (ordenación) se toman
únicamente de listas blancas definidas en el código.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from contextlib import contextmanager
from typing import Iterator

import pymysql
from pymysql.cursors import DictCursor
from flask import current_app, g

SQL_MODE = "STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SOCKET_CANDIDATES = ("/var/lib/mysql/mysql.sock", "/run/mysqld/mysqld.sock",
                     "/var/run/mysqld/mysqld.sock", "/tmp/mysql.sock")
log = logging.getLogger("ajsystem")
_local_socket: str | None = None  # socket local que funcionó cuando TCP fue rechazado


def utcnow() -> dt.datetime:
    """Hora del servidor en UTC, sin tzinfo (así se almacena en DATETIME(6))."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def connect(settings, *, user: str | None = None, password: str | None = None,
            database: str | None = "__default__", autocommit: bool = False) -> pymysql.connections.Connection:
    kwargs = dict(
        host=settings.db_host,
        port=settings.db_port,
        user=user if user is not None else settings.db_user,
        password=password if password is not None else settings.db_password,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=autocommit,
        connect_timeout=10,
        read_timeout=60,
        write_timeout=60,
        init_command=f"SET SESSION time_zone = '+00:00', SESSION sql_mode = '{SQL_MODE}'",
    )
    if database == "__default__":
        kwargs["database"] = settings.db_name
    elif database:
        kwargs["database"] = database
    if settings.db_socket:
        return pymysql.connect(unix_socket=settings.db_socket, **kwargs)
    if settings.db_ssl:
        # TLS hacia MySQL. En producción se recomienda además verificar la CA (ver docs/DESPLIEGUE.md).
        kwargs["ssl"] = {"check_hostname": False} if not settings.is_production else {}
    global _local_socket
    if _local_socket:
        return pymysql.connect(unix_socket=_local_socket, **_without_ssl(kwargs))
    try:
        return pymysql.connect(**kwargs)
    except pymysql.err.OperationalError as exc:
        # Hay alojamientos (Hostinger compartido) donde MySQL local no acepta TCP en 127.0.0.1
        # pero sí por su socket Unix. Solo se intenta con un servidor local y TCP rechazado.
        if not (exc.args and exc.args[0] == 2003 and settings.db_host in LOCAL_HOSTS):
            raise
        sock = next((p for p in SOCKET_CANDIDATES if os.path.exists(p)), None)
        if not sock:
            log.warning("MySQL rechazó TCP en %s y no hay socket local en %s",
                        settings.db_host, ", ".join(SOCKET_CANDIDATES))
            raise
        conn = pymysql.connect(unix_socket=sock, **_without_ssl(kwargs))
        _local_socket = sock
        log.warning("MySQL rechazó TCP en %s:%s; se usa el socket local %s", settings.db_host, settings.db_port, sock)
        return conn


# El socket es una conexión local: TLS no aporta nada y MySQL puede no ofrecerlo en él.
def _without_ssl(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k != "ssl"}


def get_conn() -> pymysql.connections.Connection:
    if "db_conn" not in g:
        g.db_conn = connect(current_app.config["AJS_SETTINGS"])
    return g.db_conn


def close_conn(_exc=None) -> None:
    conn = g.pop("db_conn", None)
    if conn is not None:
        try:
            conn.rollback()  # Nada queda a medias: lo no confirmado explícitamente se descarta.
        finally:
            conn.close()


@contextmanager
def transaction() -> Iterator[DictCursor]:
    """Abre una transacción: confirma solo si todo el bloque termina sin excepción."""
    if g.get("in_tx"):
        raise RuntimeError("transacciones anidadas no permitidas")
    conn = get_conn()
    conn.rollback()  # Garantiza un inicio limpio.
    cur = conn.cursor()
    g.in_tx = True
    try:
        cur.execute("START TRANSACTION")
        yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        g.in_tx = False
        cur.close()


def query_all(sql: str, params: tuple | dict = ()) -> list[dict]:
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    conn.commit()  # Cierra la instantánea de lectura (REPEATABLE READ).
    return list(rows)


def query_one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def init_app(app) -> None:
    app.teardown_appcontext(close_conn)
