"""Infraestructura de pruebas: MySQL REAL (no simulado).

Crea una base aislada `ajsys_test` con su propia cuenta de aplicación de privilegios mínimos,
aplica las migraciones y los GRANT de producción. Requiere AJS_DB_ROOT_PASSWORD (en .env).
"""
from __future__ import annotations

import os
import re
import secrets
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

from ajsystem import create_app, db, dbadmin, users  # noqa: E402
from ajsystem.config import load_settings  # noqa: E402

TEST_DB = "ajsys_test"
TEST_APP_USER = "ajsys_test_app"
PASSWORD = "Clave-de-Prueba-2026"
UA_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def _root_pw() -> str:
    pw = os.environ.get("AJS_DB_ROOT_PASSWORD")
    if not pw:
        pytest.skip("AJS_DB_ROOT_PASSWORD no definido: se requiere MySQL de pruebas")
    return pw


@pytest.fixture(scope="session")
def overrides():
    return {"db_name": TEST_DB, "db_user": TEST_APP_USER, "db_password": secrets.token_urlsafe(24),
            "db_admin_user": "ajsys_test_owner", "db_admin_password": secrets.token_urlsafe(24),
            "login_max_failures_user": 5, "login_max_failures_ip": 1000, "page_size": 5}


@pytest.fixture(scope="session")
def settings(overrides):
    return load_settings(overrides)


@pytest.fixture(scope="session")
def root_conn_factory(settings):
    pw = _root_pw()

    def factory(database=TEST_DB):
        return db.connect(settings, user="root", password=pw, database=database, autocommit=True)
    return factory


@pytest.fixture(scope="session", autouse=True)
def database(settings, root_conn_factory):
    root = root_conn_factory(None)
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB}`")
    root.close()
    pw = _root_pw()
    dbadmin.bootstrap(settings, "root", pw, log=lambda *_: None)
    dbadmin.migrate(settings, user="root", password=pw, log=lambda *_: None)
    dbadmin.apply_grants(settings, "root", pw, log=lambda *_: None)
    yield


@pytest.fixture(scope="session")
def app(overrides, database):
    application = create_app(overrides)
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def make_user(app):
    """Crea un usuario con contraseña conocida (sin cambio obligatorio) y devuelve sus datos."""
    def _make(role: str, name: str | None = None, must_change: bool = False) -> dict:
        username = (name or f"{role.lower()}_{uuid.uuid4().hex[:8]}")
        with app.app_context():
            role_id = db.query_one("SELECT id FROM roles WHERE code = %s", (role,))["id"]
            uid = users.create_user(username, f"Usuario {username}", "", PASSWORD, role_id, must_change=must_change)
        return {"id": uid, "username": username, "password": PASSWORD, "role": role}
    return _make


class Browser:
    """Cliente de pruebas que extrae el token CSRF de los formularios como haría un navegador."""

    def __init__(self, app, user_agent=UA_WINDOWS):
        self.client = app.test_client()
        self.client.environ_base["HTTP_USER_AGENT"] = user_agent
        self.client.environ_base["REMOTE_ADDR"] = "203.0.113.10"

    def csrf(self, path="/login") -> str:
        html = self.client.get(path).get_data(as_text=True)
        m = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
        assert m, f"no hay token CSRF en {path}"
        return m.group(1)

    def login(self, user: dict, password: str | None = None):
        token = self.csrf("/login")
        return self.client.post("/login", data={"username": user["username"],
                                                "password": password or user["password"], "csrf_token": token})

    def post(self, path, data=None, csrf_from="/"):
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf(csrf_from))
        return self.client.post(path, data=data)

    def get(self, path, **kw):
        return self.client.get(path, **kw)


@pytest.fixture()
def browser(app):
    def _b(user: dict | None = None, **kw) -> Browser:
        b = Browser(app, **kw)
        if user:
            r = b.login(user)
            assert r.status_code == 302, r.get_data(as_text=True)
        return b
    return _b


@pytest.fixture()
def q(app):
    """Consulta directa con la cuenta de la aplicación."""
    def _q(sql, params=()):
        with app.app_context():
            return db.query_all(sql, params)
    return _q


def new_op() -> str:
    return uuid.uuid4().hex


def create_report(b: Browser) -> int:
    r = b.post("/informes/nuevo")
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def revision_of(q, report_id: int) -> int:
    return q("SELECT revision FROM reports WHERE id = %s", (report_id,))[0]["revision"]


def fill(b: Browser, q, report_id: int, accion="guardar", **fields):
    data = {"fecha": "2026-10-19", "nombre": "Auditoría equipo", "dispositivo": "Portátil", "marca": "Dell",
            "numero_serie": "SN-001", "estado": "Activo", "disco": "Sin novedad",
            **fields, "accion": accion, "revision": revision_of(q, report_id), "op_id": new_op()}
    return b.post(f"/informes/{report_id}/guardar", data, csrf_from=f"/informes/{report_id}")


def settings_for(app):
    return replace(app.config["AJS_SETTINGS"])


@pytest.fixture()
def app_conn(app):
    s = app.config["AJS_SETTINGS"]
    conn = db.connect(s, autocommit=True)  # cuenta de la aplicación, privilegios mínimos
    yield conn
    conn.close()
