"""Ciclo de vida de los informes: crear, guardar, cerrar (último paso), reabrir, volver a cerrar."""
import json

from conftest import create_report, fill, new_op, revision_of


def status(q, rid):
    return q("SELECT status_code FROM reports WHERE id = %s", (rid,))[0]["status_code"]


def events(q, rid):
    return [r["action"] for r in q("SELECT action FROM report_events WHERE report_id = %s ORDER BY id", (rid,))]


def data(q, rid):
    return json.loads(q("SELECT data FROM reports WHERE id = %s", (rid,))[0]["data"])


def test_crear_guardar_recuperar_y_editar(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    row = q("SELECT code, owner_id, status_code, revision FROM reports WHERE id = %s", (rid,))[0]
    assert row["owner_id"] == t["id"] and row["status_code"] == "EN_ELABORACION" and row["code"].startswith("AJ-")

    assert fill(b, q, rid, nombre="Primer título", disco="Línea 1").status_code == 302
    assert status(q, rid) == "EN_ELABORACION"  # guardar NO cierra
    assert data(q, rid)["nombre"] == "Primer título"

    # Recuperación tras volver a abrir: el formulario muestra los datos persistidos
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert 'value="Primer título"' in html and "Guardar cambios" in html

    assert fill(b, q, rid, nombre="Título editado").status_code == 302
    assert data(q, rid)["nombre"] == "Título editado"
    assert revision_of(q, rid) == 3
    assert q("SELECT COUNT(*) AS n FROM report_versions WHERE report_id = %s", (rid,))[0]["n"] == 3
    assert events(q, rid) == ["CREAR", "GUARDAR", "GUARDAR"]


def test_guardar_sin_cambios_no_crea_revision(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid)
    rev = revision_of(q, rid)
    fill(b, q, rid)
    assert revision_of(q, rid) == rev


def test_conflicto_de_edicion_concurrente(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, nombre="A")
    r = b.post(f"/informes/{rid}/guardar", {"nombre": "B", "revision": 1, "accion": "guardar"}, csrf_from="/")
    assert r.status_code == 302
    assert data(q, rid)["nombre"] == "A"  # la revisión obsoleta no sobrescribe


def test_cierre_valida_obligatorios(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    r = fill(b, q, rid, accion="cerrar", nombre="")
    assert r.status_code == 422 and "Obligatorio para cerrar" in r.get_data(as_text=True)
    assert status(q, rid) == "EN_ELABORACION"


def test_cierre_bloquea_edicion_del_tecnico(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    r = fill(b, q, rid, accion="cerrar", nombre="Informe final")
    assert r.status_code == 302 and status(q, rid) == "CERRADO"
    row = q("SELECT closed_by, closed_at FROM reports WHERE id = %s", (rid,))[0]
    assert row["closed_by"] == t["id"] and row["closed_at"]
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "Guardar cambios" not in html and "Enviar informe" not in html and "Reabrir para editar" in html

    # Solicitud HTTP manual que intenta modificar el informe cerrado
    rev = revision_of(q, rid)
    r = b.post(f"/informes/{rid}/guardar", {"nombre": "MANIPULADO", "revision": rev, "accion": "guardar"})
    assert r.status_code == 302
    assert data(q, rid)["nombre"] == "Informe final" and revision_of(q, rid) == rev
    r = b.post(f"/informes/{rid}/guardar", {"nombre": "MANIPULADO", "revision": rev, "accion": "cerrar", "op_id": new_op()})
    assert data(q, rid)["nombre"] == "Informe final"
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type IN ('ACCESO_DENEGADO','OPERACION_INFORME_RECHAZADA') "
             "AND username = %s AND object_id = %s", (t["username"], str(rid)))[0]["n"] >= 2


def test_paso_de_envio_retirado(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar")
    page = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "Enviar informe" not in page and "enviado" not in page.lower()
    assert b.post(f"/informes/{rid}/enviar", {"op_id": new_op()}).status_code == 404
    assert status(q, rid) == "CERRADO" and "ENVIAR" not in events(q, rid)


def test_cierre_y_visibilidad_en_paneles(browser, make_user, q):
    t, j, a = make_user("TECNICO"), make_user("JEFE"), make_user("RESPONSABLE")
    b = browser(t)
    rid = create_report(b)
    fill(b, q, rid, nombre="Equipo de contabilidad", numero_serie="SN-VIS-77")
    code = q("SELECT code FROM reports WHERE id = %s", (rid,))[0]["code"]
    assert code not in browser(j).get("/").get_data(as_text=True)  # abierto: aún no está en «cerrados recientes»
    r = fill(b, q, rid, accion="cerrar", nombre="Equipo de contabilidad", numero_serie="SN-VIS-77")
    assert r.status_code == 302 and status(q, rid) == "CERRADO"
    for viewer in (j, a):
        bv = browser(viewer)
        home = bv.get("/").get_data(as_text=True)
        assert code in home and "Informes cerrados recientemente" in home
        assert code in bv.get("/consolidacion?serie=SN-VIS-77").get_data(as_text=True)
        assert bv.get(f"/informes/{rid}").status_code == 200
    home = b.get("/").get_data(as_text=True)
    assert code in home and "Mis informes cerrados" in home and "enviado" not in home.lower()


def test_cierre_duplicado_es_idempotente(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    rev = revision_of(q, rid)
    form = {"fecha": "2026-10-19", "nombre": "Doble clic", "dispositivo": "Portátil", "marca": "Dell",
            "numero_serie": "SN-001", "estado": "Activo", "disco": "Sin novedad", "accion": "cerrar",
            "revision": rev, "op_id": new_op()}
    assert b.post(f"/informes/{rid}/guardar", form, csrf_from=f"/informes/{rid}").status_code == 302
    # Doble clic / reintento con el mismo identificador: no se registra un segundo cierre
    assert b.post(f"/informes/{rid}/guardar", form, csrf_from=f"/informes/{rid}").status_code == 302
    assert events(q, rid).count("CERRAR") == 1 and status(q, rid) == "CERRADO"


def _closed_and_sent(browser, q, tech):
    b = browser(tech)
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar", nombre="Original")
    return b, rid


def test_reapertura_por_jefe_y_nuevo_cierre_conserva_historial(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    bt, rid = _closed_and_sent(browser, q, t)
    bj = browser(j)
    r = bj.post(f"/informes/{rid}/reabrir", {"motivo": "Falta el detalle de la memoria RAM", "op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "REABIERTO"
    ev = q("SELECT user_id, reason, from_status, to_status FROM report_events WHERE report_id=%s AND action='REABRIR'", (rid,))[0]
    assert ev["user_id"] == j["id"] and ev["from_status"] == "CERRADO" and "memoria RAM" in ev["reason"]
    audit_ev = q("SELECT detail FROM audit_log WHERE event_type='INFORME_REABIERTO' AND user_id=%s ORDER BY seq DESC LIMIT 1", (j["id"],))[0]
    det = json.loads(audit_ev["detail"])
    assert det["estado_anterior"] == "CERRADO" and det["estado_nuevo"] == "REABIERTO" and "memoria RAM" in det["motivo"]

    # Tras reabrir, el técnico autor puede editar de nuevo y volver a cerrar
    assert fill(bt, q, rid, nombre="Corregido").status_code == 302
    assert fill(bt, q, rid, accion="cerrar", nombre="Corregido").status_code == 302
    assert events(q, rid) == ["CREAR", "GUARDAR", "CERRAR", "REABRIR", "GUARDAR", "CERRAR"]
    titles = [r["title"] for r in q("SELECT title FROM report_versions WHERE report_id=%s ORDER BY revision", (rid,))]
    assert "Original" in titles and titles[-1] == "Corregido"  # las versiones anteriores siguen existiendo
    assert q("SELECT reopen_count FROM reports WHERE id=%s", (rid,))[0]["reopen_count"] == 1


def test_reapertura_por_responsable_y_edicion_directiva(browser, make_user, q):
    t, a = make_user("TECNICO"), make_user("RESPONSABLE")
    _bt, rid = _closed_and_sent(browser, q, t)
    ba = browser(a)
    assert ba.post(f"/informes/{rid}/reabrir", {"motivo": "Revisión solicitada por gerencia", "op_id": new_op()}).status_code == 302
    assert status(q, rid) == "REABIERTO"
    assert fill(ba, q, rid, nombre="Ajustado por responsable").status_code == 302
    assert q("SELECT updated_by FROM reports WHERE id=%s", (rid,))[0]["updated_by"] == a["id"]


def test_reapertura_exige_motivo(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    _bt, rid = _closed_and_sent(browser, q, t)
    r = browser(j).post(f"/informes/{rid}/reabrir", {"motivo": "corto", "op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "CERRADO"


def test_reapertura_de_informe_abierto_rechazada(browser, make_user, q):
    a = make_user("RESPONSABLE")
    b = browser(a)
    rid = create_report(b)
    b.post(f"/informes/{rid}/reabrir", {"motivo": "No debería poder reabrirse", "op_id": new_op()})
    assert status(q, rid) == "EN_ELABORACION"


def test_descarga_pdf_por_jefe(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    _bt, rid = _closed_and_sent(browser, q, t)
    r = browser(j).get(f"/informes/{rid}/pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:5] == b"%PDF-"
    assert "attachment" in r.headers["Content-Disposition"]
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type='INFORME_DESCARGADO' AND user_id=%s", (j["id"],))[0]["n"] == 1


def test_consolidacion_filtros_y_paginacion(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    bt = browser(t)
    serial = "SN-PAG-" + new_op()[:6]
    for i in range(7):
        rid = create_report(bt)
        fill(bt, q, rid, accion="cerrar", nombre=f"Lote {i}", numero_serie=serial)
    bj = browser(j)
    p1 = bj.get(f"/consolidacion?serie={serial}").get_data(as_text=True)
    assert "7 informe(s) encontrados" in p1 and "Página 1 de 2" in p1
    p2 = bj.get(f"/consolidacion?serie={serial}&page=2").get_data(as_text=True)
    assert "Página 2 de 2" in p2
    assert "7 informe(s)" in bj.get(f"/consolidacion?serie={serial}&tecnico={t['id']}&estado=CERRADO").get_data(as_text=True)
    # Un informe «enviado» antiguo cuenta como cerrado en el filtro (y el enlace antiguo ?estado=ENVIADO sigue sirviendo)
    q("UPDATE reports SET status_code = 'ENVIADO', first_sent_at = closed_at, last_sent_at = closed_at, "
      "last_sent_by = owner_id WHERE equipment_serial = %s ORDER BY id LIMIT 1", (serial,))
    assert "7 informe(s)" in bj.get(f"/consolidacion?serie={serial}&estado=CERRADO").get_data(as_text=True)
    assert "7 informe(s)" in bj.get(f"/consolidacion?serie={serial}&estado=ENVIADO").get_data(as_text=True)
    assert "Enviado" not in bj.get(f"/consolidacion?serie={serial}").get_data(as_text=True)
    assert "0 informe(s)" in bj.get(f"/consolidacion?serie={serial}&estado=REABIERTO").get_data(as_text=True)
    code = q("SELECT code FROM reports WHERE equipment_serial=%s ORDER BY id LIMIT 1", (serial,))[0]["code"]
    assert "1 informe(s)" in bj.get(f"/consolidacion?codigo={code}").get_data(as_text=True)
    # Comodines SQL en filtros se tratan como texto literal
    assert "0 informe(s)" in bj.get("/consolidacion?serie=%25%25%25_").get_data(as_text=True)


def test_escape_xss_en_datos_del_informe(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, nombre="<script>alert(1)</script>")
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
