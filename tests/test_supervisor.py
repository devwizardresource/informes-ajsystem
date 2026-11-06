"""Rol Supervisor: consulta y filtra tableros e informes de todos, sin modificar nada."""
from __future__ import annotations

import io
import re
import uuid

import pytest
from PIL import Image

from conftest import PASSWORD, create_report, fill, new_op, revision_of
from test_convalidacion_inventario import upload_matrix, xlsx

READ_PATHS = ["/", "/consolidacion", "/consolidacion?orden=marca&estado=CERRADO&marca=Dell&serie=SN&fecha_por=closed",
              "/consolidacion/exportar.xlsx", "/tablero", "/tablero?rango=7", "/tablero/convalidacion",
              "/tablero/convalidacion?estado=REGISTRADO&buscar=x", "/tablero/portatiles",
              "/tablero/portatiles?dictamen=NO_APTO&buscar=x", "/cuenta/perfil"]
FORBIDDEN_PATHS = ["/informes", "/informes/eliminados", "/informes/por-firmar", "/informes/importar/plantilla.xlsx",
                   "/registro", "/registro/sesiones", "/administracion/usuarios"]
# Lo que un Supervisor no debe poder cambiar. El registro de auditoría y las sesiones sí crecen.
STATE_SQL = {
    "informes": "SELECT id, status_code, revision, owner_id, reopen_count, deleted_at, closed_at, updated_at, updated_by, "
                "data FROM reports ORDER BY id",
    "versiones": "SELECT COUNT(*) AS n FROM report_versions",
    "eventos": "SELECT COUNT(*) AS n FROM report_events",
    "adjuntos": "SELECT public_id, kind, deleted_at, ocr_text FROM report_attachments ORDER BY id",
    "reaperturas": "SELECT id, status FROM reopen_requests ORDER BY id",
    "usuarios": "SELECT id, username, role_id, is_active, deleted_at, id_number, phone_number, password_hash FROM users "
                "ORDER BY id",
    "firmas": "SELECT user_id, sha256 FROM user_signatures ORDER BY user_id",
    "roles": "SELECT id, code, display_name FROM roles ORDER BY id",
    "permisos": "SELECT role_id, permission_id FROM role_permissions ORDER BY role_id, permission_id",
    "matrices": "SELECT COUNT(*) AS n FROM inventory_imports",
}


def state(q) -> dict:
    return {name: q(sql) for name, sql in STATE_SQL.items()}


def png() -> bytes:
    out = io.BytesIO()
    Image.effect_noise((120, 90), 60).convert("RGB").save(out, "PNG")
    return out.getvalue()


def upload_photo(b, report_id):
    return b.client.post(f"/informes/{report_id}/adjuntos", content_type="multipart/form-data",
                         data={"csrf_token": b.csrf(f"/informes/{report_id}"), "tipo": "FOTO",
                               "archivo": (io.BytesIO(png()), "foto.png")})


def role_id(q, code="SUPERVISOR"):
    return q("SELECT id FROM roles WHERE code = %s", (code,))[0]["id"]


@pytest.fixture()
def field(browser, make_user, q):
    """Un técnico con un informe abierto (con foto), otro cerrado y un Supervisor con sesión."""
    tech = make_user("TECNICO")
    bt = browser(tech)
    open_id, closed_id = create_report(bt), create_report(bt)
    serial = f"SN-{uuid.uuid4().hex[:8].upper()}"
    assert fill(bt, q, open_id, numero_serie=serial).status_code == 302
    assert fill(bt, q, closed_id, accion="cerrar", numero_serie=f"{serial}-C").status_code == 302
    photo = upload_photo(bt, open_id)
    assert photo.status_code == 201
    user = make_user("SUPERVISOR")
    return {"tech": tech, "open": open_id, "closed": closed_id, "serial": serial, "photo": photo.get_json()["id"],
            "user": user, "b": browser(user)}


def test_el_rol_solo_tiene_permisos_de_consulta(q):
    rows = q("""SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
                  JOIN roles r ON r.id = rp.role_id WHERE r.code = 'SUPERVISOR'""")
    assert {r["code"] for r in rows} == {"module.home", "module.consolidation", "report.view_all", "report.download",
                                        "dashboard.view"}
    assert q("SELECT display_name FROM roles WHERE code = 'SUPERVISOR'")[0]["display_name"] == "Supervisor"


@pytest.mark.parametrize("path", READ_PATHS)
def test_consulta_tableros_y_consolidacion(field, path):
    assert field["b"].get(path).status_code == 200


@pytest.mark.parametrize("path", FORBIDDEN_PATHS)
def test_no_entra_a_modulos_de_gestion(field, q, path):
    before = q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'ACCESO_DENEGADO' AND username = %s",
               (field["user"]["username"],))[0]["n"]
    assert field["b"].get(path).status_code == 403
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'ACCESO_DENEGADO' AND username = %s",
             (field["user"]["username"],))[0]["n"] == before + 1


def test_filtra_el_trabajo_de_un_tecnico(field, browser, make_user):
    other = browser(make_user("TECNICO"))
    other_id = create_report(other)
    b, tech_id = field["b"], field["tech"]["id"]
    html = b.get(f"/consolidacion?tecnico={tech_id}").get_data(as_text=True)
    assert f'href="/informes/{field["open"]}"' in html and f'href="/informes/{other_id}"' not in html
    html = b.get(f"/consolidacion?serie={field['serial']}-C").get_data(as_text=True)
    assert f'href="/informes/{field["closed"]}"' in html and f'href="/informes/{field["open"]}"' not in html
    assert f"Usuario {field['tech']['username']}" in b.get(f"/tablero?tecnico={tech_id}").get_data(as_text=True)


def test_menu_sin_opciones_de_escritura(field):
    b = field["b"]
    html = b.get("/").get_data(as_text=True)
    assert "Consolidación de informes" in html and "Tablero" in html and "Resumen de informes" in html
    for text in ("Creación de informe", "Crear informe nuevo", "Registro detallado", "Importar matriz", 'href="/administracion',
                 "Solicitudes de reapertura"):
        assert text not in html
    assert 'action="/tablero/convalidacion/matriz"' not in b.get("/tablero/convalidacion").get_data(as_text=True)


def test_abre_informes_ajenos_en_solo_lectura(field):
    b = field["b"]
    for report_id in (field["open"], field["closed"]):
        page = b.get(f"/informes/{report_id}")
        html = page.get_data(as_text=True)
        assert page.status_code == 200 and "Consulta de solo lectura" in html
        assert 'contenteditable="true"' not in html and "js/report.js" not in html and "data-draft-key" not in html
        for action in ('name="accion"', 'data-photo-input', "/reabrir", "/eliminar", "/firmar", "/retirar"):
            assert action not in html
    assert b.get(f"/informes/{field['open']}/adjuntos/{field['photo']}").status_code == 200


def test_descarga_el_pdf_y_queda_auditado(field, q):
    r = field["b"].get(f"/informes/{field['closed']}/pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'INFORME_DESCARGADO' AND username = %s",
             (field["user"]["username"],))[0]["n"] == 1


def test_exportar_queda_auditado(field, q):
    assert field["b"].get("/consolidacion/exportar.xlsx").status_code == 200
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'INFORMES_EXPORTADOS' AND username = %s",
             (field["user"]["username"],))[0]["n"] == 1


def post_everywhere(app, b, report_id, revision, public_id, user_id):
    """Envía un POST válido en forma a cada ruta de la aplicación (salvo la cuenta propia)."""
    values = {"report_id": report_id, "public_id": public_id, "user_id": user_id, "code": "TECNICO"}
    token = b.csrf(f"/informes/{report_id}")
    seen = []
    for rule in app.url_map.iter_rules():
        if "POST" not in rule.methods or rule.endpoint.startswith("auth."):
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", lambda m: str(values[m.group(1)]), rule.rule)
        for accion in ("guardar", "cerrar"):
            data = {"csrf_token": token, "accion": accion, "op_id": new_op(), "motivo": "Motivo suficientemente largo",
                    "nota": "Nota suficientemente larga", "decision": "aprobar", "revision": revision, "fecha": "2026-10-19",
                    "nombre": "Alterado", "dispositivo": "Portátil", "marca": "Alterada", "numero_serie": "ALTERADO",
                    "estado": "Activo", "disco": "Sin novedad", "tipo": "FOTO", "texto": "Serial ALTERADO",
                    "adjunto": public_id, "modo": "todos", "activo": "0", "confirmar": "ELIMINAR", "role_id": "1",
                    "display_name": "Alterado", "id_number": "99999999", "phone_number": "+57 300 000 0000",
                    "username": f"intruso_{uuid.uuid4().hex[:6]}", "full_name": "Intruso", "password": PASSWORD,
                    "archivo": (io.BytesIO(png()), "foto.png"), "firma": (io.BytesIO(png()), "firma.png")}
            r = b.client.post(path, data=data, content_type="multipart/form-data")
            assert r.status_code < 500, rule.rule
            if rule.endpoint != "reports.preview_pdf":  # la vista previa es de lectura: muestra lo guardado
                assert r.status_code in (302, 400, 403, 404, 422), (rule.rule, r.status_code)
            seen.append(rule.endpoint)
    return set(seen)


@pytest.mark.parametrize("target", ["open", "closed"])
def test_ninguna_ruta_post_cambia_datos(app, field, q, target):
    """Recorre el mapa de rutas: también cubre las rutas que se agreguen más adelante."""
    before = state(q)
    seen = post_everywhere(app, field["b"], field[target], revision_of(q, field[target]), field["photo"],
                           field["tech"]["id"])
    assert {"reports.save", "reports.attachment_add", "reports.attachment_remove", "reports.autofill", "reports.reopen",
            "reports.request_reopen", "reports.resolve_request", "reports.delete", "reports.sign_closed",
            "reports.sign_batch", "reports.create", "reports.import_matrix", "dashboard.upload_matrix",
            "admin.users_create", "admin.users_role", "logs.verify"} <= seen
    assert state(q) == before


def test_tecnico_que_pasa_a_supervisor_no_edita_los_suyos(browser, make_user, q):
    director, tech = browser(make_user("RESPONSABLE")), make_user("TECNICO")
    bt = browser(tech)
    open_id, closed_id = create_report(bt), create_report(bt)
    assert fill(bt, q, closed_id, accion="cerrar").status_code == 302
    assert director.post(f"/administracion/usuarios/{tech['id']}/rol", {"role_id": role_id(q)},
                         csrf_from="/administracion/usuarios").status_code == 302
    assert bt.get("/").status_code == 302  # el cambio de rol revoca la sesión abierta

    b = browser(tech)
    before = state(q)
    assert fill(b, q, open_id, nombre="Alterado").status_code == 302  # rechazado: vuelve al informe
    assert fill(b, q, open_id, accion="cerrar").status_code == 302
    assert upload_photo(b, open_id).status_code == 403
    assert b.post(f"/informes/{closed_id}/reabrir", {"motivo": "Motivo suficientemente largo", "op_id": new_op()},
                  csrf_from=f"/informes/{closed_id}").status_code == 403
    assert "Consulta de solo lectura" in b.get(f"/informes/{open_id}").get_data(as_text=True)
    assert state(q) == before


def test_no_carga_firma_en_su_perfil(field, q):
    b = field["b"]
    assert "firma-perfil" not in b.get("/cuenta/perfil").get_data(as_text=True)
    r = b.client.post("/cuenta/perfil/firma", content_type="multipart/form-data",
                      data={"csrf_token": b.csrf("/"),
                            "firma": (io.BytesIO(png()), "firma.png")})
    assert r.status_code == 403
    assert not q("SELECT 1 FROM user_signatures WHERE user_id = %s", (field["user"]["id"],))


@pytest.mark.parametrize("role", ["TECNICO", "JEFE", "RESPONSABLE"])
def test_los_demas_perfiles_conservan_su_firma_de_perfil(browser, make_user, q, role):
    u = make_user(role)
    b = browser(u)
    assert "firma-perfil" in b.get("/cuenta/perfil").get_data(as_text=True)
    r = b.client.post("/cuenta/perfil/firma", content_type="multipart/form-data",
                      data={"csrf_token": b.csrf("/cuenta/perfil"), "firma": (io.BytesIO(png()), "firma.png")})
    assert r.status_code == 302
    assert q("SELECT 1 FROM user_signatures WHERE user_id = %s", (u["id"],))


def test_el_director_crea_supervisores(browser, make_user, q):
    director = browser(make_user("RESPONSABLE"))
    assert ">Supervisor</option>" in director.get("/administracion/usuarios").get_data(as_text=True)
    username = f"sup_{uuid.uuid4().hex[:8]}"
    r = director.post("/administracion/usuarios", {"username": username, "full_name": "Supervisora de Campo",
                                                   "password": PASSWORD, "role_id": role_id(q)},
                      csrf_from="/administracion/usuarios")
    assert r.status_code == 302
    assert q("""SELECT r.code FROM users u JOIN roles r ON r.id = u.role_id WHERE u.username = %s""",
             (username,))[0]["code"] == "SUPERVISOR"


def test_alta_por_consola(app, q, monkeypatch):
    monkeypatch.setenv("AJS_NEW_USER_PASSWORD", PASSWORD)
    username = f"sup_{uuid.uuid4().hex[:8]}"
    result = app.test_cli_runner().invoke(args=["create-user", "--username", username, "--full-name", "Por Consola",
                                                "--role", "SUPERVISOR"])
    assert result.exit_code == 0, result.output
    assert q("SELECT id FROM users WHERE username = %s", (username,))


def test_ve_la_auditoria_de_portatiles_con_matriz(browser, make_user, q, field):
    director = browser(make_user("RESPONSABLE"))
    raw = xlsx([[1, field["serial"], 1, "DELL INSPIRON 15 PULGADAS CORE i5", "Contrato#1"]])
    assert upload_matrix(director, raw).status_code == 302
    html = field["b"].get("/tablero/portatiles").get_data(as_text=True)
    assert field["serial"] in html and f'href="/informes/{field["open"]}"' in html
