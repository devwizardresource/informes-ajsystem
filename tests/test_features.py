"""Plantilla oficial, fotos/OCR, importación Excel, solicitudes de reapertura, identificación y sesiones."""
import io
import re
from urllib.parse import parse_qs, urlsplit

import pymysql
import pytest
from PIL import Image

from conftest import PASSWORD, create_report, fill, new_op, revision_of

from ajsystem.reports import extract, schema
from ajsystem.security import passwords


def _jpeg(exif_gps=False, size=(640, 480)) -> bytes:
    img = Image.new("RGB", size, "#3355aa")
    out = io.BytesIO()
    if exif_gps:
        exif = Image.Exif()
        exif[0x010F] = "CamaraPrueba"  # Make
        exif.get_ifd(0x8825).update({1: "N", 2: (4.0, 36.0, 0.0)})  # GPSInfo
        img.save(out, "JPEG", exif=exif)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


def upload(b, rid, data: bytes, kind="FOTO", name="foto.jpg"):
    return b.client.post(f"/informes/{rid}/adjuntos", data={
        "csrf_token": b.csrf(f"/informes/{rid}"), "tipo": kind, "archivo": (io.BytesIO(data), name)},
        content_type="multipart/form-data")


# ---------------------------------------------------------------- contraseñas e identificación
@pytest.mark.parametrize("pw,ok", [
    ("Clave-Segura-2026", True),
    ("clave-segura-2026", False),   # sin mayúscula
    ("CLAVE-SEGURA-2026", False),   # sin minúscula
    ("Clave-Segura-Sin", False),    # sin número
    ("ClaveSegura2026x", False),    # sin símbolo
    ("Cl4ve-corta", False),         # menos de 12
])
def test_politica_de_contrasena(pw, ok):
    assert (passwords.validate_policy(pw) == []) is ok


def test_login_con_numero_de_identificacion(app, browser, q):
    from ajsystem import users, db
    with app.app_context():
        role = db.query_one("SELECT id FROM roles WHERE code = 'TECNICO'")["id"]
        users.create_user("María José Pérez", "María José Pérez", "1.020.304.050", PASSWORD, role, must_change=False)
    row = q("SELECT username, id_number FROM users WHERE id_number = '1020304050'")[0]
    assert row["username"] == "maría josé pérez"  # el nombre completo sirve como usuario
    b = browser()
    assert b.login({"username": "1020304050", "password": PASSWORD}).status_code == 302
    b2 = browser()
    assert b2.login({"username": "María José Pérez", "password": PASSWORD}).status_code == 302
    # Un fallo con la identificación no revela el usuario asociado
    r = browser().login({"username": "1020304050", "password": "Incorrecta-123!"})
    assert r.status_code == 401 and "maría josé pérez" not in r.get_data(as_text=True)


def test_usuario_e_identificacion_no_se_cruzan(app):
    from ajsystem import db, users
    with app.app_context():
        role = db.query_one("SELECT id FROM roles WHERE code = 'TECNICO'")["id"]
        users.create_user("cruce_a", "Cruce A", "55667788", PASSWORD, role)
        with pytest.raises(users.UserError):
            users.create_user("55667788", "Cruce B", "", PASSWORD, role)  # usuario = identificación ajena
        with pytest.raises(users.UserError):
            users.create_user("cruce_c", "Cruce C", "cruce_a", PASSWORD, role)


def test_sin_correo_en_usuarios(q):
    cols = {r["COLUMN_NAME"] for r in q("SELECT COLUMN_NAME FROM information_schema.columns "
                                       "WHERE table_schema = DATABASE() AND table_name = 'users'")}
    assert "email" not in cols and "id_number" in cols


# ---------------------------------------------------------------- reglas de edición del técnico
def test_tecnico_edita_tras_guardar_y_pierde_edicion_al_cerrar(browser, make_user, q):
    t = make_user("TECNICO")
    bt = browser(t)
    rid = create_report(bt)
    assert fill(bt, q, rid, nombre="Primera").status_code == 302
    assert fill(bt, q, rid, nombre="Segunda").status_code == 302  # sigue editando tras guardar
    assert fill(bt, q, rid, accion="cerrar", nombre="Final").status_code == 302
    rev = revision_of(q, rid)
    fill(bt, q, rid, nombre="Intento tras cierre")
    assert revision_of(q, rid) == rev
    assert upload(bt, rid, _jpeg()).status_code == 403  # tampoco admite fotos
    # Cualquier perfil puede reabrir con motivo sin pedir aprobación de un tercero.
    r = bt.post(f"/informes/{rid}/reabrir", {"motivo": "Corregir el número de serie", "op_id": new_op()},
                csrf_from=f"/informes/{rid}")
    assert r.status_code == 302
    assert q("SELECT status_code FROM reports WHERE id = %s", (rid,))[0]["status_code"] == "REABIERTO"
    assert fill(bt, q, rid, nombre="Corregido").status_code == 302
    assert q("SELECT title FROM reports WHERE id = %s", (rid,))[0]["title"] == "Corregido"


def test_observaciones_solo_se_muestran_al_seleccionarlas():
    from ajsystem.reports import schema, sheet

    template = schema.load_template("aj_v1")
    values = schema.empty_data(template)
    values["observaciones"] = "Nota opcional"
    values["observaciones_habilitadas"] = False
    hidden = sheet.render(template, values, editable=True)
    assert 'data-conditional-field="observaciones" hidden' in hidden
    values["observaciones_habilitadas"] = True
    shown = sheet.render(template, values, editable=True)
    assert 'data-conditional-field="observaciones" hidden' not in shown
    assert "Nota opcional" in shown
    assert "Medidas de protección contra robos" not in shown


def test_firma_de_perfil_se_copia_al_informe_al_cerrar(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    response = b.client.post("/cuenta/perfil/firma", data={
        "csrf_token": b.csrf("/cuenta/perfil"), "firma": (io.BytesIO(_jpeg()), "firma.jpg")},
        content_type="multipart/form-data")
    assert response.status_code == 302
    signature = q("SELECT sha256 FROM user_signatures WHERE user_id = %s", (t["id"],))[0]
    rid = create_report(b)
    assert fill(b, q, rid, accion="cerrar").status_code == 302
    copied = q("SELECT sha256, created_by FROM report_attachments "
               "WHERE report_id = %s AND kind = 'FIRMA_ELABORA' AND deleted_at IS NULL", (rid,))[0]
    assert copied["sha256"] == signature["sha256"] and copied["created_by"] == t["id"]


def test_responsable_carga_firma_del_tecnico_y_se_usa_al_cerrar(browser, make_user, q):
    t, boss, admin_user = make_user("TECNICO"), make_user("JEFE"), make_user("RESPONSABLE")
    admin = browser(admin_user)

    def send(b, user_id):
        return b.client.post(f"/administracion/usuarios/{user_id}/firma", data={
            "csrf_token": b.csrf("/"),
            "firma": (io.BytesIO(_jpeg()), "firma.jpg")}, content_type="multipart/form-data")

    assert send(admin, t["id"]).status_code == 302
    assert q("SELECT COUNT(*) AS n FROM user_signatures WHERE user_id = %s", (t["id"],))[0]["n"] == 1
    # Solo cuentas de rol Técnico, y solo quien gestiona usuarios.
    assert send(admin, boss["id"]).status_code == 302
    assert q("SELECT COUNT(*) AS n FROM user_signatures WHERE user_id = %s", (boss["id"],))[0]["n"] == 0
    assert send(browser(boss), t["id"]).status_code == 403
    bt = browser(t)
    rid = create_report(bt)
    assert fill(bt, q, rid, accion="cerrar").status_code == 302
    assert q("SELECT COUNT(*) AS n FROM report_attachments WHERE report_id = %s AND kind = 'FIRMA_ELABORA' "
             "AND deleted_at IS NULL", (rid,))[0]["n"] == 1


def test_firma_de_quien_recibe_solo_jefe_y_responsable(browser, make_user):
    t, boss = make_user("TECNICO"), make_user("JEFE")
    owner_browser = browser(t)
    rid = create_report(owner_browser)
    assert upload(owner_browser, rid, _jpeg(), kind="FIRMA_RECIBE").status_code == 403
    manager = browser(boss)
    assert upload(manager, rid, _jpeg(), kind="FIRMA_RECIBE").status_code == 201


def test_invitacion_whatsapp_crea_contrasena_de_primer_acceso(browser, make_user, q):
    admin = browser(make_user("RESPONSABLE"))
    role_id = q("SELECT id FROM roles WHERE code = 'TECNICO'")[0]["id"]
    created = admin.post("/administracion/usuarios", {
        "username": "invitado_prueba", "full_name": "Invitado Prueba", "id_number": "", "role_id": role_id,
        "phone_number": "+57 300 123 4567", "password": "Clave-Temporal-456!"},
        csrf_from="/administracion/usuarios")
    assert created.status_code == 302
    user_id = q("SELECT id FROM users WHERE username = 'invitado_prueba'")[0]["id"]
    sent = admin.post(f"/administracion/usuarios/{user_id}/invitacion-whatsapp", csrf_from="/administracion/usuarios")
    assert sent.status_code == 303 and sent.headers["Location"].startswith("https://wa.me/573001234567?")
    message = parse_qs(urlsplit(sent.headers["Location"]).query)["text"][0]
    token = re.search(r"/invitacion/([A-Za-z0-9_-]+)", message).group(1)
    first_access = browser()
    path = f"/invitacion/{token}"
    response = first_access.post(path, {"new": "Clave-Personal-789!", "confirm": "Clave-Personal-789!"}, csrf_from=path)
    assert response.status_code == 302
    assert q("SELECT must_change_password FROM users WHERE id = %s", (user_id,))[0]["must_change_password"] == 0
    assert browser().login({"username": "invitado_prueba", "password": "Clave-Personal-789!"}).status_code == 302
    assert first_access.get(path).status_code == 410


# ---------------------------------------------------------------- registro de sesiones
def test_registro_de_sesiones_solo_jefe_y_responsable(browser, make_user):
    t = make_user("TECNICO")
    assert browser(t).get("/registro/sesiones").status_code == 403
    for role in ("JEFE", "RESPONSABLE"):
        html = browser(make_user(role)).get("/registro/sesiones").get_data(as_text=True)
        assert t["username"] in html and "203.0.113.10" in html and "Windows 10/11" in html


# ---------------------------------------------------------------- fotos y autocompletado
def test_foto_se_anexa_sin_metadatos_y_es_privada(browser, make_user, q):
    t = make_user("TECNICO")
    bt = browser(t)
    rid = create_report(bt)
    r = upload(bt, rid, _jpeg(exif_gps=True))
    assert r.status_code == 201, r.get_data(as_text=True)
    pid = r.get_json()["id"]
    img = bt.get(f"/informes/{rid}/adjuntos/{pid}")
    assert img.status_code == 200 and img.mimetype == "image/jpeg"
    assert not Image.open(io.BytesIO(img.data)).getexif()  # sin EXIF ni GPS
    # Todos los perfiles ven todos los informes; sin sesión la foto no se entrega.
    anon = browser().get(f"/informes/{rid}/adjuntos/{pid}")
    assert anon.status_code in (302, 401, 404) and anon.mimetype != "image/jpeg"
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'ADJUNTO_AGREGADO' AND object_id = "
             "(SELECT code FROM reports WHERE id = %s)", (rid,))[0]["n"] == 1


def test_archivo_no_imagen_rechazado(browser, make_user):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    assert upload(b, rid, b"<?php echo 1; ?>", name="foto.jpg").status_code == 415
    assert upload(b, rid, b"%PDF-1.4 falso", name="x.png").status_code == 415


def test_adjuntos_no_se_borran_en_la_base(browser, make_user, q, app_conn):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    upload(b, rid, _jpeg())
    with app_conn.cursor() as cur:
        with pytest.raises(pymysql.err.OperationalError):
            cur.execute("DELETE FROM report_attachments WHERE report_id = %s", (rid,))


def test_autocompletar_desde_texto_de_foto(browser, make_user):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    texto = "Fabricante del sistema: Dell Inc.\nModelo del sistema: Latitude 5410\nNúmero de serie: 7HG2K53\n" \
            "Procesador Intel(R) Core(TM) i5-10210U CPU @ 1.60GHz\nRAM instalada 8,00 GB"
    r = b.client.post(f"/informes/{rid}/autocompletar", data={"csrf_token": b.csrf(f"/informes/{rid}"), "texto": texto})
    campos = r.get_json()["campos"]
    assert campos["marca"] == "Dell" and campos["numero_serie"] == "7HG2K53" and campos["modelo"] == "Latitude 5410"
    assert campos["dispositivo"] == "Portátil" and campos["ram"].startswith("8,00 GB")


@pytest.mark.parametrize("texto,esperado", [
    ("S/N: MXL8341ABC\nModel No.: TPC-F112-SF\nHP ProDesk 600 G4", {"numero_serie": "MXL8341ABC", "marca": "HP"}),
    ("Hardware Vendor: Lenovo\nHardware Model: ThinkSystem SR630", {"marca": "Lenovo", "dispositivo": "Servidor"}),
    ("Temperatura CPU 54 °C\nCPU Fan 2350 RPM", {"temperatura": "54 °C", "ventiladores": "Operativo · 2350 RPM"}),
])
def test_extractor_de_texto(texto, esperado):
    out = extract.from_text(texto, schema.load_template("aj_v1"))
    for k, v in esperado.items():
        assert out.get(k) == v, out


# ---------------------------------------------------------------- importación Excel / CSV
def test_importar_excel_crea_un_informe_por_equipo(browser, make_user, q):
    from openpyxl import Workbook
    t = make_user("TECNICO")
    b = browser(t)
    wb = Workbook()
    ws = wb.active
    ws.append(["Nombre", "Dispositivo", "Marca", "N.º de serie", "Modelo", "Estado"])
    ws.append(["PC-01", "Portátil", "hewlett-packard", "SNX-001", "EliteBook 840", "activo"])
    ws.append(["PC-02", "Computador de escritorio", "Lenovo", "SNX-002", "ThinkCentre M70", "Obsoleto"])
    ws.append([None, None, None, None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    r = b.client.post("/informes/importar", data={"csrf_token": b.csrf("/informes"),
                                                  "archivo": (io.BytesIO(buf.getvalue()), "equipos.xlsx")},
                      content_type="multipart/form-data")
    assert r.status_code == 302
    rows = q("SELECT title, equipment_brand, equipment_serial, data FROM reports WHERE owner_id = %s ORDER BY id",
             (t["id"],))
    assert [(x["title"], x["equipment_brand"], x["equipment_serial"]) for x in rows] == [
        ("PC-01", "HP", "SNX-001"), ("PC-02", "Lenovo", "SNX-002")]
    assert '"estado": "Activo"' in rows[0]["data"]


def test_importar_csv_y_archivo_invalido(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    csv_data = "Nombre;Marca;Número de serie\nSRV-01;Dell;ABC123\n".encode("utf-8")
    b.client.post("/informes/importar", data={"csrf_token": b.csrf("/informes"),
                                              "archivo": (io.BytesIO(csv_data), "equipos.csv")},
                  content_type="multipart/form-data")
    assert q("SELECT COUNT(*) AS n FROM reports WHERE owner_id = %s", (t["id"],))[0]["n"] == 1
    r = b.client.post("/informes/importar", data={"csrf_token": b.csrf("/informes"),
                                                  "archivo": (io.BytesIO(b"PK\x03\x04basura"), "x.xlsx")},
                      content_type="multipart/form-data", follow_redirects=True)
    assert "Excel" in r.get_data(as_text=True)
    assert q("SELECT COUNT(*) AS n FROM reports WHERE owner_id = %s", (t["id"],))[0]["n"] == 1


def test_plantilla_de_importacion_descargable(browser, make_user):
    r = browser(make_user("TECNICO")).get("/informes/importar/plantilla.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"


# ---------------------------------------------------------------- PDF con anexo
def test_previsualizacion_pdf_no_guarda_y_se_muestra_en_linea(browser, make_user, q):
    pytest.importorskip("weasyprint")
    owner = make_user("RESPONSABLE")
    b = browser(owner)
    rid = create_report(b)
    token = b.csrf(f"/informes/{rid}")
    r = b.client.post(f"/informes/{rid}/pdf/preview", data={
        "csrf_token": token,
        "fecha": "2026-10-19",
        "nombre": "Equipo solo en previsualización",
        "dispositivo": "Portátil",
        "marca": "Dell",
        "numero_serie": "PREVIEW-001",
        "estado": "Activo",
    })
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    assert r.headers["Content-Disposition"].startswith("inline;")
    row = q("SELECT title, equipment_serial FROM reports WHERE id = %s", (rid,))[0]
    assert row == {"title": "", "equipment_serial": None}


def test_pdf_incluye_anexo_fotografico(browser, make_user, q):
    pytest.importorskip("weasyprint")
    t, j = make_user("TECNICO"), make_user("JEFE")
    bt = browser(t)
    rid = create_report(bt)
    upload(bt, rid, _jpeg())
    fill(bt, q, rid, accion="cerrar")
    r = browser(j).get(f"/informes/{rid}/pdf")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    from weasyprint import HTML
    from ajsystem import db, documents
    from ajsystem.reports import attachments, service
    with j_app(browser).app_context():
        report = db.query_one("SELECT r.*, o.full_name AS owner_name, o.username AS owner_username "
                              "FROM reports r JOIN users o ON o.id = r.owner_id WHERE r.id = %s", (rid,))
        import json
        report["data"] = json.loads(report["data"])
        doc = documents.build_document_data(report, [], {"full_name": "x"}, db.utcnow(), "UTC",
                                            attachments.for_document(rid))
        html = documents.render_html(doc, "aj_v1.html")
    assert "ANEXO FOTOGRÁFICO" in html
    pages = HTML(string=html, base_url=str(documents.DOC_TEMPLATES_DIR),
                 url_fetcher=documents._local_only_fetcher()).render().pages
    assert len(pages) == 2  # formato en una hoja + anexo


def j_app(browser):
    return browser().client.application


# ---------------------------------------------------------------- plantilla de resources/
def test_la_hoja_es_la_plantilla_de_resources(browser, make_user):
    from ajsystem.reports import sheet
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    html_page = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert '<main class="sheet">' in html_page and "INFORME DE INVENTARIO Y CONCILIACIÓN" in html_page
    assert "Imprimir / Guardar como PDF" in html_page and "Registro de quien realizó el informe" in html_page
    css = b.get("/informes/plantilla.css")
    assert css.status_code == 200 and css.get_data(as_text=True) == sheet.SOURCE.read_text(encoding="utf-8").split(
        "<style>")[1].split("</style>")[0]


def test_valores_escapados_en_la_hoja(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, nombre='"><script>alert(1)</script>')
    page = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


# ---------------------------------------------------------------- eliminación por el admin
def test_admin_elimina_y_restaura_informes(browser, make_user, q):
    t, admin, j = make_user("TECNICO"), make_user("RESPONSABLE"), make_user("JEFE")
    bt = browser(t)
    rid = create_report(bt)
    fill(bt, q, rid, nombre="Equipo a eliminar")
    # Ni el técnico ni el jefe pueden eliminar
    assert bt.post(f"/informes/{rid}/eliminar", {"motivo": "Informe duplicado por error", "op_id": new_op()}).status_code == 403
    assert browser(j).post(f"/informes/{rid}/eliminar", {"motivo": "Informe duplicado por error",
                                                         "op_id": new_op()}).status_code == 403
    ba = browser(admin)
    ba.post(f"/informes/{rid}/eliminar", {"motivo": "corto", "op_id": new_op()}, csrf_from=f"/informes/{rid}")
    assert q("SELECT deleted_at FROM reports WHERE id = %s", (rid,))[0]["deleted_at"] is None  # motivo obligatorio
    r = ba.post(f"/informes/{rid}/eliminar", {"motivo": "Informe duplicado por error", "op_id": new_op()},
                csrf_from=f"/informes/{rid}")
    assert r.status_code == 302
    # Desaparece para todos, incluido su autor, y queda auditado
    assert bt.get(f"/informes/{rid}").status_code == 404
    assert browser(j).get(f"/informes/{rid}").status_code == 404
    assert "Equipo a eliminar" not in bt.get("/informes").get_data(as_text=True)
    assert "Equipo a eliminar" in ba.get("/informes/eliminados").get_data(as_text=True)
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'INFORME_ELIMINADO'")[0]["n"] >= 1
    # Se restaura
    assert ba.post(f"/informes/{rid}/restaurar", {}, csrf_from="/informes/eliminados").status_code == 302
    assert bt.get(f"/informes/{rid}").status_code == 200
