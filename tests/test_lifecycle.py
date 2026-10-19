"""Ciclo de vida de los informes: crear, guardar, cerrar, enviar, reabrir, volver a cerrar."""
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

    assert fill(b, q, rid, titulo="Primer título", observaciones="Línea 1").status_code == 302
    assert status(q, rid) == "EN_ELABORACION"  # guardar NO cierra
    assert data(q, rid)["titulo"] == "Primer título"

    # Recuperación tras volver a abrir: el formulario muestra los datos persistidos
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert 'value="Primer título"' in html and "Guardar cambios" in html

    assert fill(b, q, rid, titulo="Título editado").status_code == 302
    assert data(q, rid)["titulo"] == "Título editado"
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
    fill(b, q, rid, titulo="A")
    r = b.post(f"/informes/{rid}/guardar", {"titulo": "B", "revision": 1, "accion": "guardar"}, csrf_from="/")
    assert r.status_code == 302
    assert data(q, rid)["titulo"] == "A"  # la revisión obsoleta no sobrescribe


def test_cierre_valida_obligatorios(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    r = fill(b, q, rid, accion="cerrar", titulo="")
    assert r.status_code == 422 and "Obligatorio para cerrar" in r.get_data(as_text=True)
    assert status(q, rid) == "EN_ELABORACION"


def test_cierre_bloquea_edicion_del_tecnico(browser, make_user, q):
    t = make_user("TECNICO")
    b = browser(t)
    rid = create_report(b)
    r = fill(b, q, rid, accion="cerrar", titulo="Informe final")
    assert r.status_code == 302 and status(q, rid) == "CERRADO"
    row = q("SELECT closed_by, closed_at FROM reports WHERE id = %s", (rid,))[0]
    assert row["closed_by"] == t["id"] and row["closed_at"]
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "Guardar cambios" not in html and "Enviar informe" in html

    # Solicitud HTTP manual que intenta modificar el informe cerrado
    rev = revision_of(q, rid)
    r = b.post(f"/informes/{rid}/guardar", {"titulo": "MANIPULADO", "revision": rev, "accion": "guardar"})
    assert r.status_code == 302
    assert data(q, rid)["titulo"] == "Informe final" and revision_of(q, rid) == rev
    r = b.post(f"/informes/{rid}/guardar", {"titulo": "MANIPULADO", "revision": rev, "accion": "cerrar", "op_id": new_op()})
    assert data(q, rid)["titulo"] == "Informe final"
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type IN ('ACCESO_DENEGADO','OPERACION_INFORME_RECHAZADA') "
             "AND username = %s AND object_id = %s", (t["username"], str(rid)))[0]["n"] >= 2


def test_rechaza_envio_de_informe_abierto(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid)
    r = b.post(f"/informes/{rid}/enviar", {"op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "EN_ELABORACION"
    assert "ENVIAR" not in events(q, rid)


def test_envio_y_visibilidad_en_paneles(browser, make_user, q):
    t, j, a = make_user("TECNICO"), make_user("JEFE"), make_user("RESPONSABLE")
    b = browser(t)
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar", titulo="Equipo de contabilidad", numero_serie="SN-VIS-77")
    code = q("SELECT code FROM reports WHERE id = %s", (rid,))[0]["code"]
    # Antes de enviar: no visible para los destinatarios
    assert code not in browser(j).get("/").get_data(as_text=True)
    r = b.post(f"/informes/{rid}/enviar", {"op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "ENVIADO"
    row = q("SELECT last_sent_by, last_sent_at, first_sent_at FROM reports WHERE id = %s", (rid,))[0]
    assert row["last_sent_by"] == t["id"] and row["last_sent_at"] and row["first_sent_at"]
    for viewer in (j, a):
        bv = browser(viewer)
        assert code in bv.get("/").get_data(as_text=True)
        assert code in bv.get("/consolidacion?serie=SN-VIS-77").get_data(as_text=True)
        assert bv.get(f"/informes/{rid}").status_code == 200
    # El técnico lo ve como enviado en su Inicio
    assert code in b.get("/").get_data(as_text=True)


def test_envio_duplicado(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar")
    op = new_op()
    assert b.post(f"/informes/{rid}/enviar", {"op_id": op}).status_code == 302
    # Doble clic / reintento con el mismo identificador: idempotente
    assert b.post(f"/informes/{rid}/enviar", {"op_id": op}).status_code == 302
    # Nuevo intento con otro identificador: rechazado (ya enviado)
    assert b.post(f"/informes/{rid}/enviar", {"op_id": new_op()}).status_code == 302
    assert events(q, rid).count("ENVIAR") == 1
    assert q("SELECT COUNT(*) AS n FROM audit_log WHERE event_type='INFORME_ENVIADO' AND object_id = "
             "(SELECT code FROM reports WHERE id=%s)", (rid,))[0]["n"] == 1


def _closed_and_sent(browser, q, tech):
    b = browser(tech)
    rid = create_report(b)
    fill(b, q, rid, accion="cerrar", titulo="Original")
    b.post(f"/informes/{rid}/enviar", {"op_id": new_op()})
    return b, rid


def test_reapertura_por_jefe_y_nuevo_cierre_conserva_historial(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    bt, rid = _closed_and_sent(browser, q, t)
    bj = browser(j)
    r = bj.post(f"/informes/{rid}/reabrir", {"motivo": "Falta el detalle de la memoria RAM", "op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "REABIERTO"
    ev = q("SELECT user_id, reason, from_status, to_status FROM report_events WHERE report_id=%s AND action='REABRIR'", (rid,))[0]
    assert ev["user_id"] == j["id"] and ev["from_status"] == "ENVIADO" and "memoria RAM" in ev["reason"]
    audit_ev = q("SELECT detail FROM audit_log WHERE event_type='INFORME_REABIERTO' AND user_id=%s ORDER BY seq DESC LIMIT 1", (j["id"],))[0]
    det = json.loads(audit_ev["detail"])
    assert det["estado_anterior"] == "ENVIADO" and det["estado_nuevo"] == "REABIERTO" and "memoria RAM" in det["motivo"]

    # Tras reabrir, el técnico autor puede editar de nuevo y volver a cerrar y enviar
    assert fill(bt, q, rid, titulo="Corregido").status_code == 302
    assert fill(bt, q, rid, accion="cerrar", titulo="Corregido").status_code == 302
    assert bt.post(f"/informes/{rid}/enviar", {"op_id": new_op()}).status_code == 302
    assert events(q, rid) == ["CREAR", "GUARDAR", "CERRAR", "ENVIAR", "REABRIR", "GUARDAR", "CERRAR", "ENVIAR"]
    titles = [r["title"] for r in q("SELECT title FROM report_versions WHERE report_id=%s ORDER BY revision", (rid,))]
    assert "Original" in titles and titles[-1] == "Corregido"  # las versiones anteriores siguen existiendo
    assert q("SELECT reopen_count FROM reports WHERE id=%s", (rid,))[0]["reopen_count"] == 1


def test_reapertura_por_responsable_y_edicion_directiva(browser, make_user, q):
    t, a = make_user("TECNICO"), make_user("RESPONSABLE")
    _bt, rid = _closed_and_sent(browser, q, t)
    ba = browser(a)
    assert ba.post(f"/informes/{rid}/reabrir", {"motivo": "Revisión solicitada por gerencia", "op_id": new_op()}).status_code == 302
    assert status(q, rid) == "REABIERTO"
    assert fill(ba, q, rid, titulo="Ajustado por responsable").status_code == 302
    assert q("SELECT updated_by FROM reports WHERE id=%s", (rid,))[0]["updated_by"] == a["id"]


def test_reapertura_exige_motivo(browser, make_user, q):
    t, j = make_user("TECNICO"), make_user("JEFE")
    _bt, rid = _closed_and_sent(browser, q, t)
    r = browser(j).post(f"/informes/{rid}/reabrir", {"motivo": "corto", "op_id": new_op()})
    assert r.status_code == 302 and status(q, rid) == "ENVIADO"


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
        fill(bt, q, rid, accion="cerrar", titulo=f"Lote {i}", numero_serie=serial)
        bt.post(f"/informes/{rid}/enviar", {"op_id": new_op()})
    bj = browser(j)
    p1 = bj.get(f"/consolidacion?serie={serial}").get_data(as_text=True)
    assert "7 informe(s) encontrados" in p1 and "Página 1 de 2" in p1
    p2 = bj.get(f"/consolidacion?serie={serial}&page=2").get_data(as_text=True)
    assert "Página 2 de 2" in p2
    assert "7 informe(s)" in bj.get(f"/consolidacion?serie={serial}&tecnico={t['id']}&estado=ENVIADO").get_data(as_text=True)
    assert "0 informe(s)" in bj.get(f"/consolidacion?serie={serial}&estado=REABIERTO").get_data(as_text=True)
    code = q("SELECT code FROM reports WHERE equipment_serial=%s ORDER BY id LIMIT 1", (serial,))[0]["code"]
    assert "1 informe(s)" in bj.get(f"/consolidacion?codigo={code}").get_data(as_text=True)
    # Comodines SQL en filtros se tratan como texto literal
    assert "0 informe(s)" in bj.get("/consolidacion?serie=%25%25%25_").get_data(as_text=True)


def test_escape_xss_en_datos_del_informe(browser, make_user, q):
    b = browser(make_user("TECNICO"))
    rid = create_report(b)
    fill(b, q, rid, titulo="<script>alert(1)</script>")
    html = b.get(f"/informes/{rid}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
