"""Autorización por rol y por recurso, aplicada en el servidor."""
import pytest

from conftest import create_report, fill, new_op


def denied_count(q, username):
    return q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'ACCESO_DENEGADO' AND username = %s",
             (username,))[0]["n"]


@pytest.mark.parametrize("path", ["/registro", "/administracion/usuarios"])
def test_tecnico_no_accede_a_modulos_restringidos(browser, make_user, q, path):
    u = make_user("TECNICO")
    b = browser(u)
    before = denied_count(q, u["username"])
    r = b.get(path)
    assert r.status_code == 403
    assert denied_count(q, u["username"]) == before + 1


def test_tecnico_no_ve_enlaces_restringidos(browser, make_user):
    html = browser(make_user("TECNICO")).get("/").get_data(as_text=True)
    assert "Creación de informe" in html
    assert "Consolidación de informes" in html and "Registro detallado" not in html


def test_tecnico_no_puede_descargar(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser(u)
    rid = create_report(b)
    r = b.get(f"/informes/{rid}/pdf")
    assert r.status_code == 403 and r.mimetype != "application/pdf"
    assert "Descargar PDF" not in b.get(f"/informes/{rid}").get_data(as_text=True)
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type='INFORME_DESCARGADO' AND username=%s",
             (u["username"],))[0]["n"] == 0


def test_tecnico_puede_reabrir_con_motivo_sin_aprobacion(browser, make_user, q):
    u = make_user("TECNICO")
    b = browser(u)
    rid = create_report(b)
    assert fill(b, q, rid, accion="cerrar").status_code == 302
    r = b.post(f"/informes/{rid}/reabrir", {"motivo": "Quiero cambiar algo importante", "op_id": new_op()})
    assert r.status_code == 302
    assert q("SELECT status_code FROM reports WHERE id = %s", (rid,))[0]["status_code"] == "REABIERTO"


def test_tecnico_puede_ver_y_editar_informes_ajenos(browser, make_user, q):
    a, b_user = make_user("TECNICO"), make_user("TECNICO")
    ba = browser(a)
    rid = create_report(ba)
    bb = browser(b_user)
    assert bb.get(f"/informes/{rid}").status_code == 200
    r = fill(bb, q, rid, nombre="Editado por otro técnico")
    assert r.status_code == 302
    assert q("SELECT title FROM reports WHERE id = %s", (rid,))[0]["title"] == "Editado por otro técnico"


@pytest.mark.parametrize("role", ["JEFE", "RESPONSABLE"])
def test_jefe_y_responsable_ven_y_editan_cualquier_informe(browser, make_user, q, role):
    """Regla de negocio: el jefe y el responsable pueden consultar y editar todos los informes."""
    t, boss = make_user("TECNICO"), make_user(role)
    rid = create_report(browser(t))  # borrador nunca enviado
    bj = browser(boss)
    assert bj.get(f"/informes/{rid}").status_code == 200
    assert fill(bj, q, rid, nombre="Corregido por dirección").status_code == 302
    assert q("SELECT title FROM reports WHERE id = %s", (rid,))[0]["title"] == "Corregido por dirección"


def test_jefe_no_gestiona_usuarios(browser, make_user):
    b = browser(make_user("JEFE"))
    assert b.get("/administracion/usuarios").status_code == 403
    r = b.post("/administracion/usuarios", {"username": "x_nuevo", "full_name": "X", "password": "Clave-Larga-12345!", "role_id": 1})
    assert r.status_code == 403


@pytest.mark.parametrize("role", ["JEFE", "RESPONSABLE"])
def test_perfiles_directivos_acceden_a_los_cuatro_modulos(browser, make_user, role):
    b = browser(make_user(role))
    for path in ("/", "/informes", "/consolidacion", "/registro"):
        assert b.get(path).status_code == 200, path


def test_responsable_gestiona_usuarios_y_queda_auditado(browser, make_user, q):
    admin = make_user("RESPONSABLE")
    b = browser(admin)
    r = b.post("/administracion/usuarios", {"username": "tecnico_nuevo_x", "full_name": "Técnico Nuevo",
                                            "password": "Clave-Temporal-123", "role_id": 3}, csrf_from="/administracion/usuarios")
    assert r.status_code == 302
    new_id = q("SELECT id FROM users WHERE username = 'tecnico_nuevo_x'")[0]["id"]
    r = b.post(f"/administracion/usuarios/{new_id}/rol", {"role_id": 2}, csrf_from="/administracion/usuarios")
    assert r.status_code == 302
    ev = q("SELECT detail FROM audit_log WHERE event_type = 'PERMISOS_ROL_CAMBIADO' AND object_id = 'tecnico_nuevo_x'")
    assert ev and '"rol_nuevo": "JEFE"' in ev[0]["detail"]


def test_cambio_de_rol_revoca_sesiones(browser, make_user, q):
    admin, t = make_user("RESPONSABLE"), make_user("TECNICO")
    bt = browser(t)
    assert bt.get("/").status_code == 200
    browser(admin).post(f"/administracion/usuarios/{t['id']}/rol", {"role_id": 2}, csrf_from="/administracion/usuarios")
    assert bt.get("/").status_code == 302  # su sesión anterior ya no es válida


def test_cuenta_desactivada_no_inicia_sesion(browser, make_user):
    admin, t = make_user("RESPONSABLE"), make_user("TECNICO")
    browser(admin).post(f"/administracion/usuarios/{t['id']}/estado", {"activo": "0"}, csrf_from="/administracion/usuarios")
    assert browser().login(t).status_code == 401
