"""Persistencia: reinicio, transacciones/reversiones y copias de seguridad."""
import os
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


def test_fallo_en_envio_mantiene_cerrado_y_permite_reintentar(app, browser, make_user, q, monkeypatch):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar")
    original = audit.write
    calls = {"n": 0}

    def fail_once(cur, event_type, *a, **kw):
        if event_type == audit.REPORT_SENT and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("fallo simulado durante el envío")
        return original(cur, event_type, *a, **kw)

    monkeypatch.setattr(audit, "write", fail_once)
    op = new_op()
    assert b.post(f"/informes/{rid}/enviar", {"op_id": op}).status_code == 500
    assert q("SELECT status_code FROM reports WHERE id=%s", (rid,))[0]["status_code"] == "CERRADO"
    assert b.post(f"/informes/{rid}/enviar", {"op_id": op}).status_code == 302  # reintento
    assert q("SELECT status_code FROM reports WHERE id=%s", (rid,))[0]["status_code"] == "ENVIADO"
    assert q("SELECT COUNT(*) AS n FROM report_events WHERE report_id=%s AND action='ENVIAR'", (rid,))[0]["n"] == 1


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
