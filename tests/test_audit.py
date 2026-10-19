"""Integridad del registro de auditoría y de las tablas de historial."""
import json

import pymysql
import pytest

from ajsystem import audit, db
from conftest import create_report, fill, new_op


@pytest.fixture()
def app_conn(app):
    s = app.config["AJS_SETTINGS"]
    conn = db.connect(s, autocommit=True)  # cuenta de la aplicación, privilegios mínimos
    yield conn
    conn.close()


@pytest.mark.parametrize("sql", [
    "UPDATE audit_log SET outcome = 'EXITO' WHERE seq = 1",
    "DELETE FROM audit_log WHERE seq = 1",
    "UPDATE report_versions SET title = 'x' WHERE id = 1",
    "DELETE FROM report_events WHERE id = 1",
    "DELETE FROM reports WHERE id = 1",
    "DROP TRIGGER trg_audit_no_update",
    "TRUNCATE TABLE audit_log",
])
def test_cuenta_de_aplicacion_no_puede_alterar_historial(app_conn, sql):
    with app_conn.cursor() as cur, pytest.raises(pymysql.err.MySQLError) as exc:
        cur.execute(sql)
    assert exc.value.args[0] in (1142, 1143, 1227, 1044, 1370)  # sin privilegio


def test_disparadores_bloquean_incluso_a_cuentas_privilegiadas(root_conn_factory, browser, make_user):
    browser(make_user("TECNICO"))  # asegura al menos un evento
    conn = root_conn_factory()
    with conn.cursor() as cur:
        for sql in ("UPDATE audit_log SET outcome='FALLO' ORDER BY seq LIMIT 1", "DELETE FROM audit_log ORDER BY seq LIMIT 1"):
            with pytest.raises(pymysql.err.OperationalError) as exc:
                cur.execute(sql)
            assert exc.value.args[0] == 1644 and "solo insercion" in exc.value.args[1]
    conn.close()


def test_db_bloquea_contenido_de_informe_cerrado(browser, make_user, q, app_conn):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar")
    with app_conn.cursor() as cur:
        with pytest.raises(pymysql.err.OperationalError) as exc:
            cur.execute("UPDATE reports SET title = 'alterado' WHERE id = %s", (rid,))
        assert "bloqueado" in exc.value.args[1]
        with pytest.raises(pymysql.err.OperationalError) as exc:
            cur.execute("UPDATE reports SET status_code = 'EN_ELABORACION' WHERE id = %s", (rid,))
        assert "transicion" in exc.value.args[1]


def test_restricciones_de_integridad(app_conn, make_user):
    u = make_user("TECNICO")
    with app_conn.cursor() as cur:
        with pytest.raises(pymysql.err.IntegrityError):  # estado inexistente (clave foránea)
            cur.execute("""INSERT INTO reports (code, owner_id, status_code, template_code, data, created_at, updated_at, updated_by)
                           VALUES ('X-1', %s, 'INVENTADO', 't', '{}', NOW(), NOW(), %s)""", (u["id"], u["id"]))
        with pytest.raises(pymysql.err.OperationalError):  # CHECK: cerrado sin datos de cierre
            cur.execute("""INSERT INTO reports (code, owner_id, status_code, template_code, data, created_at, updated_at, updated_by)
                           VALUES ('X-2', %s, 'CERRADO', 't', '{}', NOW(), NOW(), %s)""", (u["id"], u["id"]))
        with pytest.raises(pymysql.err.IntegrityError):  # propietario inexistente
            cur.execute("""INSERT INTO reports (code, owner_id, status_code, template_code, data, created_at, updated_at, updated_by)
                           VALUES ('X-3', 999999, 'EN_ELABORACION', 't', '{}', NOW(), NOW(), 999999)""")


def test_numero_de_serie_no_es_unico(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    for _ in range(2):
        fill(b, q, create_report(b), numero_serie="SN-REPETIDO-1")
    assert q("SELECT COUNT(*) AS n FROM reports WHERE equipment_serial = 'SN-REPETIDO-1'")[0]["n"] >= 2


def test_cadena_integra_y_verificacion_desde_la_web(browser, make_user, q, app):
    j = make_user("JEFE")
    b = browser(j)
    with app.app_context():
        assert audit.verify_chain(db.get_conn(), app.config["AJS_SETTINGS"].audit_hmac_key)["ok"]
    r = b.post("/registro/verificar", csrf_from="/registro")
    assert r.status_code == 302
    assert "Integridad verificada" in b.get("/registro").get_data(as_text=True)


def test_registro_detallado_es_de_solo_lectura(browser, make_user):
    html = browser(make_user("RESPONSABLE")).get("/registro").get_data(as_text=True)
    for word in ("Eliminar", "Editar", "/registro/borrar"):
        assert word not in html


def test_operaciones_quedan_auditadas(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    fill(b, q, rid)
    fill(b, q, rid, accion="cerrar")
    b.post(f"/informes/{rid}/enviar", {"op_id": new_op()})
    b.post("/logout")
    types = [r["event_type"] for r in q("SELECT event_type FROM audit_log WHERE username=%s ORDER BY seq", (t["username"],))]
    for expected in ("LOGIN_EXITOSO", "INFORME_CREADO", "INFORME_GUARDADO", "INFORME_CERRADO", "INFORME_ENVIADO", "LOGOUT"):
        assert expected in types, expected


def _with_triggers_dropped(root_conn_factory, fn):
    """Simula a un atacante con privilegios de DBA que desactiva los disparadores."""
    conn = root_conn_factory()
    with conn.cursor() as cur:
        cur.execute("SHOW CREATE TRIGGER trg_audit_no_update")
        upd = cur.fetchone()["SQL Original Statement"]
        cur.execute("SHOW CREATE TRIGGER trg_audit_no_delete")
        dele = cur.fetchone()["SQL Original Statement"]
        cur.execute("DROP TRIGGER trg_audit_no_update")
        cur.execute("DROP TRIGGER trg_audit_no_delete")
        try:
            fn(cur)
        finally:
            cur.execute(upd)
            cur.execute(dele)
    conn.close()


def test_detecta_alteracion_de_contenido(app, browser, make_user, root_conn_factory):
    browser(make_user("TECNICO"))
    key = app.config["AJS_SETTINGS"].audit_hmac_key
    with app.app_context():
        target = db.query_one("SELECT seq, outcome FROM audit_log WHERE event_type = 'LOGIN_EXITOSO' ORDER BY seq DESC LIMIT 1")

    def tamper(cur):
        cur.execute("UPDATE audit_log SET outcome = 'FALLO' WHERE seq = %s", (target["seq"],))

    def restore(cur):
        cur.execute("UPDATE audit_log SET outcome = %s WHERE seq = %s", (target["outcome"], target["seq"]))

    _with_triggers_dropped(root_conn_factory, tamper)
    try:
        with app.app_context():
            result = audit.verify_chain(db.get_conn(), key)
        assert not result["ok"]
        assert any(e["seq"] == target["seq"] and "HMAC" in e["problema"] for e in result["errors"])
    finally:
        _with_triggers_dropped(root_conn_factory, restore)
    with app.app_context():
        assert audit.verify_chain(db.get_conn(), key)["ok"]


def test_detecta_eliminacion_del_ultimo_registro(app, browser, make_user, root_conn_factory):
    browser(make_user("TECNICO"))
    key = app.config["AJS_SETTINGS"].audit_hmac_key
    conn = root_conn_factory()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM audit_log ORDER BY seq DESC LIMIT 1")
        last = cur.fetchone()
    conn.close()
    cols = [c for c in last if c != "id"]

    def delete(cur):
        cur.execute("DELETE FROM audit_log WHERE seq = %s", (last["seq"],))

    def reinsert(cur):
        cur.execute(f"INSERT INTO audit_log (id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))})",
                    (last["id"], *[last[c] for c in cols]))

    _with_triggers_dropped(root_conn_factory, delete)
    try:
        with app.app_context():
            result = audit.verify_chain(db.get_conn(), key)
        assert not result["ok"] and any("eliminación" in e["problema"] for e in result["errors"])
    finally:
        _with_triggers_dropped(root_conn_factory, reinsert)
    with app.app_context():
        assert audit.verify_chain(db.get_conn(), key)["ok"]


def test_clave_hmac_distinta_no_valida_la_cadena(app, browser, make_user):
    browser(make_user("TECNICO"))
    with app.app_context():
        assert not audit.verify_chain(db.get_conn(), "otra-clave-" + "x" * 40)["ok"]


def test_detalle_saneado_sin_secretos(app):
    clean = audit._sanitize({"password": "x", "csrf_token": "y", "session_cookie": "z", "motivo": "ok", "valor": 1.5})
    assert clean == {"motivo": "ok", "valor": "1.5"}
    assert json.dumps(clean)
