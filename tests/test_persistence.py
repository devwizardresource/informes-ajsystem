"""Persistencia: reinicio, transacciones/reversiones y copias de seguridad."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ajsystem import audit, create_app
from ajsystem.backup import create_backup, restore_backup
from conftest import Browser, create_report, fill, new_op

ROOT = Path(__file__).resolve().parent.parent


def test_datos_persisten_tras_reiniciar_la_aplicacion(app, overrides, make_user, q):
    t = make_user("TECNICO")
    b = Browser(app)
    b.login(t)
    rid = create_report(b)
    fill(b, q, rid, nombre="Sobrevive al reinicio")
    # Proceso nuevo e independiente (otro intérprete): nada depende de la memoria del proceso anterior.
    code = f"""
import sys; sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(ROOT / 'tests')!r})
from ajsystem import create_app
from conftest import Browser
app = create_app({overrides!r})
b = Browser(app)
r = b.login({{"username": {t['username']!r}, "password": {t['password']!r}}})
assert r.status_code == 302, r.status_code
html = b.get('/informes/{rid}').get_data(as_text=True)
assert 'Sobrevive al reinicio' in html
print('OK')
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env=os.environ.copy())
    assert out.returncode == 0 and "OK" in out.stdout, out.stderr[-2000:]


def test_reversion_si_falla_la_auditoria_durante_el_cierre(app, browser, make_user, q, monkeypatch):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, nombre="Antes")
    rev = q("SELECT revision FROM reports WHERE id=%s", (rid,))[0]["revision"]
    original = audit.write

    def failing(cur, event_type, *a, **kw):
        if event_type == audit.REPORT_CLOSED:
            raise RuntimeError("fallo simulado de escritura de auditoría")
        return original(cur, event_type, *a, **kw)

    monkeypatch.setattr(audit, "write", failing)
    r = fill(b, q, rid, accion="cerrar", nombre="Después")
    assert r.status_code == 500
    row = q("SELECT status_code, revision, title FROM reports WHERE id=%s", (rid,))[0]
    assert row == {"status_code": "EN_ELABORACION", "revision": rev, "title": "Antes"}
    assert q("SELECT COUNT(*) AS n FROM report_events WHERE report_id=%s AND action='CERRAR'", (rid,))[0]["n"] == 0
    assert q("SELECT COUNT(*) AS n FROM report_versions WHERE report_id=%s", (rid,))[0]["n"] == rev


def test_fallo_en_cierre_no_pierde_datos_y_permite_reintentar(app, browser, make_user, q, monkeypatch):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, nombre="Datos guardados")
    original = audit.write
    calls = {"n": 0}

    def fail_once(cur, event_type, *a, **kw):
        if event_type == audit.REPORT_CLOSED and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("fallo simulado durante el cierre")
        return original(cur, event_type, *a, **kw)

    monkeypatch.setattr(audit, "write", fail_once)
    rev = q("SELECT revision FROM reports WHERE id=%s", (rid,))[0]["revision"]
    form = {"fecha": "2026-10-19", "nombre": "Datos guardados", "dispositivo": "Portátil", "marca": "Dell",
            "numero_serie": "SN-001", "estado": "Activo", "disco": "Sin novedad", "accion": "cerrar",
            "revision": rev, "op_id": new_op()}
    assert b.post(f"/informes/{rid}/guardar", form, csrf_from=f"/informes/{rid}").status_code == 500
    row = q("SELECT status_code, revision, title FROM reports WHERE id=%s", (rid,))[0]
    assert row == {"status_code": "EN_ELABORACION", "revision": rev, "title": "Datos guardados"}  # todo revertido
    assert b.post(f"/informes/{rid}/guardar", form, csrf_from=f"/informes/{rid}").status_code == 302  # reintento
    assert q("SELECT status_code FROM reports WHERE id=%s", (rid,))[0]["status_code"] == "CERRADO"
    assert q("SELECT COUNT(*) AS n FROM report_events WHERE report_id=%s AND action='CERRAR'", (rid,))[0]["n"] == 1


def test_copia_y_restauracion(app, settings, browser, make_user, q, root_conn_factory, tmp_path):
    if not os.environ.get("AJS_DB_ROOT_PASSWORD"):
        pytest.skip("requiere cuenta DBA")
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar", nombre="Respaldado")
    s = app.config["AJS_SETTINGS"]
    dump = create_backup(s, "root", os.environ["AJS_DB_ROOT_PASSWORD"], str(tmp_path), log=lambda *_: None)
    assert oct(dump.stat().st_mode & 0o777) == "0o600"
    target = "ajsys_restore_test"
    conn = root_conn_factory(None)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{target}`")
    conn.close()
    result = restore_backup(s, str(dump), target, "root", os.environ["AJS_DB_ROOT_PASSWORD"], log=lambda *_: None)
    assert result["row_mismatches"] == {} and result["audit_chain_ok"] and result["audit_head_matches"]
    conn = root_conn_factory(target)
    with conn.cursor() as cur:
        cur.execute("SELECT title, status_code FROM reports WHERE id = %s", (rid,))
        assert cur.fetchone() == {"title": "Respaldado", "status_code": "CERRADO"}
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.triggers WHERE trigger_schema = %s", (target,))
        assert cur.fetchone()["n"] == 13  # los disparadores de integridad también se restauran
        cur.execute(f"DROP DATABASE `{target}`")
    conn.close()


def test_restauracion_rechaza_copia_alterada(app, tmp_path, browser, make_user):
    s = app.config["AJS_SETTINGS"]
    dump = create_backup(s, "root", os.environ["AJS_DB_ROOT_PASSWORD"], str(tmp_path), log=lambda *_: None)
    with open(dump, "ab") as fh:
        fh.write(b"basura")
    from ajsystem.backup import BackupError
    with pytest.raises(BackupError, match="SHA-256"):
        restore_backup(s, str(dump), "ajsys_restore_bad", "root", os.environ["AJS_DB_ROOT_PASSWORD"], log=lambda *_: None)


def test_restauracion_no_sobrescribe_base_activa(app, tmp_path):
    s = app.config["AJS_SETTINGS"]
    dump = create_backup(s, "root", os.environ["AJS_DB_ROOT_PASSWORD"], str(tmp_path), log=lambda *_: None)
    from ajsystem.backup import BackupError
    with pytest.raises(BackupError, match="base activa"):
        restore_backup(s, str(dump), s.db_name, "root", os.environ["AJS_DB_ROOT_PASSWORD"], log=lambda *_: None)


def test_configuracion_insegura_rechazada(monkeypatch):
    from ajsystem.config import ConfigError, load_settings
    with pytest.raises(ConfigError):
        load_settings({"secret_key": "corta"})
    with pytest.raises(ConfigError):
        load_settings({"env": "production", "cookie_secure": False})


def test_create_app_falla_con_plantilla_invalida(monkeypatch, overrides):
    monkeypatch.setenv("AJS_REPORT_TEMPLATE", "no_existe")
    from ajsystem.reports.schema import TemplateError
    with pytest.raises(TemplateError):
        create_app(overrides)


def test_migracion_interrumpida_se_reanuda_sin_perder_datos(root_conn_factory, tmp_path):
    """Un arranque cortado a mitad de una migración no debe bloquear los siguientes arranques."""
    from ajsystem.migrations import apply_migrations

    root = root_conn_factory(None)
    with root.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS ajsys_test_resume")
        cur.execute("CREATE DATABASE ajsys_test_resume CHARACTER SET utf8mb4")
    root.close()
    (tmp_path / "0001_base.sql").write_text(
        "CREATE TABLE t (id INT PRIMARY KEY) ENGINE=InnoDB;\nINSERT INTO t VALUES (1);\n", encoding="utf-8")
    (tmp_path / "0002_cambio.sql").write_text(
        "ALTER TABLE t ADD COLUMN nota VARCHAR(20) NULL;\n"
        "CREATE TABLE t2 (id INT PRIMARY KEY) ENGINE=InnoDB;\n"
        "ALTER TABLE t ADD KEY ix_nota (nota);\n", encoding="utf-8")
    conn = root_conn_factory("ajsys_test_resume")
    try:
        assert apply_migrations(conn, tmp_path, log=lambda *_: None) == ["0001_base.sql", "0002_cambio.sql"]
        with conn.cursor() as cur:  # estado de un corte tras las dos primeras sentencias de 0002
            cur.execute("DELETE FROM schema_migrations WHERE version = '0002'")
            cur.execute("ALTER TABLE t DROP KEY ix_nota")
        assert apply_migrations(conn, tmp_path, log=lambda *_: None) == ["0002_cambio.sql"]
        with conn.cursor() as cur:
            cur.execute("SHOW INDEX FROM t WHERE Key_name = 'ix_nota'")
            assert cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM t")
            assert cur.fetchone()["n"] == 1
        assert apply_migrations(conn, tmp_path, log=lambda *_: None) == []
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP DATABASE IF EXISTS ajsys_test_resume")
        conn.close()


def test_error_con_plantilla_rota_no_deja_la_pagina_en_blanco(app, browser, make_user, monkeypatch):
    """Código y plantillas de versiones distintas (despliegue a medias): la respuesta nunca queda vacía."""
    import ajsystem

    def broken(*_a, **_k):
        raise RuntimeError("plantilla de otra versión")

    b = browser(make_user("TECNICO"))
    monkeypatch.setattr("ajsystem.web.home.render_template", broken)
    monkeypatch.setattr(ajsystem, "render_template", broken)
    r = b.get("/")
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and r.mimetype == "text/html"
    assert "Error 500" in body and "Volver al inicio" in body


def _expire_sessions(root_conn_factory, user_id):
    conn = root_conn_factory()
    with conn.cursor() as cur:
        cur.execute("UPDATE sessions SET last_seen_at = last_seen_at - INTERVAL 2 HOUR WHERE user_id = %s AND ended_at IS NULL",
                    (user_id,))
    conn.close()


def test_guardar_con_sesion_caducada_vuelve_al_informe_sin_tocar_datos(app, browser, make_user, q, root_conn_factory):
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    assert fill(b, q, rid, nombre="Primera versión").status_code == 302
    token = b.csrf(f"/informes/{rid}")
    revision = q("SELECT revision FROM reports WHERE id = %s", (rid,))[0]["revision"]
    _expire_sessions(root_conn_factory, t["id"])
    r = b.client.post(f"/informes/{rid}/guardar", headers={"Referer": f"http://localhost/informes/{rid}"},
                      data={"csrf_token": token, "accion": "guardar", "revision": revision, "nombre": "Perdido"})
    # Antes: error 400 genérico y lo escrito se perdía. Ahora: a iniciar sesión y de vuelta al informe.
    assert r.status_code == 303
    assert "/login" in r.headers["Location"] and f"next=/informes/{rid}" in r.headers["Location"]
    row = q("SELECT revision, JSON_UNQUOTE(JSON_EXTRACT(data, '$.nombre')) AS nombre FROM reports WHERE id = %s", (rid,))[0]
    assert row["revision"] == revision and row["nombre"] == "Primera versión"
    login_page = b.get(r.headers["Location"]).get_data(as_text=True)
    assert f'value="/informes/{rid}"' in login_page
    token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', login_page).group(1)
    r = b.client.post("/login", data={"username": t["username"], "password": t["password"],
                                      "csrf_token": token, "next": f"/informes/{rid}"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/informes/{rid}")


def test_latido_mantiene_la_sesion_y_detecta_la_caducidad(browser, make_user, root_conn_factory):
    t = make_user("TECNICO")
    b = browser(t)
    r = b.get("/sesion/latido", headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.get_json() == {"activa": True}
    _expire_sessions(root_conn_factory, t["id"])
    r = b.get("/sesion/latido", headers={"Accept": "application/json"})
    assert r.status_code == 401 and r.get_json() == {"activa": False}


def test_el_tecnico_consulta_todos_sus_informes(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    abiertos = [create_report(b) for _ in range(2)]
    cerrado = create_report(b)
    assert fill(b, q, cerrado, accion="cerrar").status_code == 302
    html = b.get("/informes").get_data(as_text=True)
    for rid in abiertos + [cerrado]:
        code = q("SELECT code FROM reports WHERE id = %s", (rid,))[0]["code"]
        assert code in html
        assert b.get(f"/informes/{rid}").status_code == 200
