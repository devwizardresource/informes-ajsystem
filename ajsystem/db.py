"""Acceso a MySQL: conexión por solicitud, transacciones explícitas y consultas parametrizadas.

Regla del proyecto: ninguna consulta concatena valores del usuario; siempre se usan
parámetros (%s) de PyMySQL. Los nombres de columna dinámicos (ordenación) se toman
únicamente de listas blancas definidas en el código.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Iterator

import pymysql
from pymysql.cursors import DictCursor
from flask import current_app, g

SQL_MODE = "STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"


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
    if settings.db_ssl:
        # TLS hacia MySQL. En producción se recomienda además verificar la CA (ver docs/DESPLIEGUE.md).
        kwargs["ssl"] = {"check_hostname": False} if not settings.is_production else {}
    return pymysql.connect(**kwargs)


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
