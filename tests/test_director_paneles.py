"""Rol Director, firma de informes cerrados, panel de actividad del técnico y tablero ejecutivo."""
import datetime as dt
import io
import json

from conftest import create_report, fill

from ajsystem import analytics
from test_features import _jpeg, upload


def _profile_signature(b):
    r = b.client.post("/cuenta/perfil/firma", data={
        "csrf_token": b.csrf("/cuenta/perfil"), "firma": (io.BytesIO(_jpeg()), "firma.jpg")},
        content_type="multipart/form-data")
    assert r.status_code == 302


def _closed_report(browser, make_user, q, **fields):
    t = make_user("TECNICO")
    bt = browser(t)
    rid = create_report(bt)
    assert fill(bt, q, rid, accion="cerrar", **fields).status_code == 302
    return t, bt, rid


def _active_receive(q, rid):
    return q("SELECT created_by FROM report_attachments WHERE report_id = %s AND kind = 'FIRMA_RECIBE' "
             "AND deleted_at IS NULL", (rid,))


# ---------------------------------------------------------------- rol Director
def test_el_rol_responsable_se_llama_director(browser, make_user, q):
    assert q("SELECT display_name FROM roles WHERE code = 'RESPONSABLE'")[0]["display_name"] == "Director"
    page = browser(make_user("RESPONSABLE")).get("/").get_data(as_text=True)
    assert "Director" in page and "Responsable administrativo" not in page


# ---------------------------------------------------------------- firma del Director en informes cerrados
def test_director_firma_informe_cerrado_con_su_firma_de_perfil(browser, make_user, q):
    _t, _bt, rid = _closed_report(browser, make_user, q)
    before = q("SELECT data, revision, status_code FROM reports WHERE id = %s", (rid,))[0]
    director = make_user("RESPONSABLE")
    bd = browser(director)
    home = bd.get("/").get_data(as_text=True)
    assert "pendientes de mi firma" in home and f"/informes/{rid}" in home
    page = bd.get(f"/informes/{rid}").get_data(as_text=True)
    assert "Firma del Director" in page and "Cargar mi firma en Mi perfil" in page
    _profile_signature(bd)
    assert bd.post(f"/informes/{rid}/firmar", csrf_from=f"/informes/{rid}").status_code == 302
    assert _active_receive(q, rid) == [{"created_by": director["id"]}]
    after = q("SELECT data, revision, status_code FROM reports WHERE id = %s", (rid,))[0]
    assert after == before  # firmar no altera el contenido ni el estado del informe cerrado
    assert f"/informes/{rid}" not in bd.get("/").get_data(as_text=True).split("pendientes de mi firma")[1].split("</section>")[0]
    # Volver a firmar reemplaza la firma vigente; la anterior queda retirada (no borrada).
    assert bd.post(f"/informes/{rid}/firmar", csrf_from=f"/informes/{rid}").status_code == 302
    rows = q("SELECT deleted_at FROM report_attachments WHERE report_id = %s AND kind = 'FIRMA_RECIBE'", (rid,))
    assert len(rows) == 2 and sum(r["deleted_at"] is None for r in rows) == 1


def test_director_carga_imagen_de_firma_en_informe_enviado(browser, make_user, q):
    _t, bt, rid = _closed_report(browser, make_user, q)
    assert bt.post(f"/informes/{rid}/enviar", {"op_id": "a" * 32}, csrf_from=f"/informes/{rid}").status_code == 302
    bd = browser(make_user("RESPONSABLE"))
    r = upload(bd, rid, _jpeg(), kind="FIRMA_RECIBE", name="firma.png")
    assert r.status_code == 201, r.get_data(as_text=True)
    assert len(_active_receive(q, rid)) == 1
    # El informe sigue cerrado para todo lo demás: no admite fotos.
    assert upload(bd, rid, _jpeg()).status_code == 403


def test_solo_el_director_firma_informes_cerrados(browser, make_user, q):
    t, bt, rid = _closed_report(browser, make_user, q)
    bj = browser(make_user("JEFE"))
    _profile_signature(bj)
    assert bj.post(f"/informes/{rid}/firmar", csrf_from="/").status_code == 403
    assert upload(bj, rid, _jpeg(), kind="FIRMA_RECIBE").status_code == 403
    assert upload(bt, rid, _jpeg(), kind="FIRMA_RECIBE").status_code == 403
    assert bt.post(f"/informes/{rid}/firmar", csrf_from="/").status_code == 403
    assert _active_receive(q, rid) == []


def test_director_no_firma_por_esta_via_un_informe_abierto(browser, make_user, q):
    bt = browser(make_user("TECNICO"))
    rid = create_report(bt)
    bd = browser(make_user("RESPONSABLE"))
    _profile_signature(bd)
    assert bd.post(f"/informes/{rid}/firmar", csrf_from="/").status_code == 302
    assert _active_receive(q, rid) == []


# ---------------------------------------------------------------- panel de actividad del técnico
def test_panel_de_actividad_solo_cuenta_los_informes_propios(browser, make_user, q):
    t1, t2 = make_user("TECNICO"), make_user("TECNICO")
    b1, b2 = browser(t1), browser(t2)
    for _ in range(3):
        create_report(b1)
    create_report(b2)
    page = b1.get("/").get_data(as_text=True)
    assert "Mi actividad" in page and "<b>3</b> informes creados" in page
    assert page.count('class="hm-cell') >= 365
    assert "3 informes · " in page  # información emergente del día de hoy
    assert "<b>1</b> informe creado" in b2.get("/").get_data(as_text=True)
    # El panel es del técnico: el Director y el jefe no lo ven.
    assert "Mi actividad" not in browser(make_user("RESPONSABLE")).get("/").get_data(as_text=True)
    assert "Mi actividad" not in browser(make_user("JEFE")).get("/").get_data(as_text=True)


def test_calendario_de_actividad_estructura(app, make_user):
    t = make_user("TECNICO")
    with app.app_context():
        cal = analytics.activity_calendar(t["id"], "America/Bogota", today=dt.date(2026, 10, 26))
    assert cal["total"] == 0 and cal["best"] is None and cal["current_streak"] == 0
    assert cal["cells"][0]["row"] == 0 and cal["cells"][-1]["date"] == "2026-10-26"
    assert max(c["col"] for c in cal["cells"]) == 52
    assert analytics.level(0, 5) == 0 and analytics.level(1, 5) == 1 and analytics.level(5, 5) == 4


# ---------------------------------------------------------------- tablero ejecutivo
def test_tablero_solo_para_el_director(browser, make_user):
    assert browser(make_user("TECNICO")).get("/tablero").status_code == 403
    assert browser(make_user("JEFE")).get("/tablero").status_code == 403
    assert browser().get("/tablero").status_code == 302  # sin sesión: a iniciar sesión
    r = browser(make_user("RESPONSABLE")).get("/tablero")
    assert r.status_code == 200 and "Tablero ejecutivo" in r.get_data(as_text=True)


def test_tablero_calcula_metricas_reales_sin_duplicar_equipos(browser, make_user, q, app):
    t = make_user("TECNICO")
    bt = browser(t)
    r1 = create_report(bt)
    fill(bt, q, r1, accion="cerrar", numero_serie="DUP-1", marca="HP", estado="Obsoleto",
         observaciones_habilitadas="1", observaciones="Bisagra rota")
    r2 = create_report(bt)  # mismo equipo, informe más reciente: el inventario usa este
    fill(bt, q, r2, numero_serie="DUP-1", marca="HP", estado="Activo")
    r3 = create_report(bt)
    fill(bt, q, r3, numero_serie="OTRO-2", marca="Dell", estado="")  # estado faltante
    assert upload(bt, r3, _jpeg()).status_code == 201
    today = dt.date.today()
    with app.app_context():
        d = analytics.executive(today - dt.timedelta(days=1), today + dt.timedelta(days=1), t["id"], "UTC")
    k = d["kpi"]
    assert k["reports"] == 3 and k["equipment"] == 2 and k["duplicates"] == 1
    assert k["bad"] == 0 and k["condition_missing"] == 1 and k["with_photos"] == 1 and k["with_obs"] == 0
    assert k["closed"] == 1 and k["pending_signature"] == 1 and k["close_hours"] is not None
    assert {c["label"]: c["n"] for c in d["condition"]} == {"Activo": 1, "Sin dato": 1}
    assert [t_["n"] for t_ in d["technicians"]] == [3]
    # Un técnico sin informes: cero real en conteos, «sin datos» en promedios que no tienen base.
    other = make_user("TECNICO")
    with app.app_context():
        empty = analytics.executive(today, today, other["id"], "UTC")
    assert empty["kpi"]["reports"] == 0 and empty["kpi"]["per_tech"] is None and empty["kpi"]["close_hours"] is None
    assert empty["technicians"][0]["n"] == 0 and empty["technicians"][0]["photos_pct"] is None
    # La página muestra los mismos datos y no modifica los informes.
    before = q("SELECT id, revision, data FROM reports WHERE owner_id = %s ORDER BY id", (t["id"],))
    page = browser(make_user("RESPONSABLE")).get(f"/tablero?tecnico={t['id']}&rango=7").get_data(as_text=True)
    assert "Bisagra rota" not in page  # su equipo tiene un informe posterior sin observaciones
    assert "OTRO-2" in page and "Sin dato" in page
    assert q("SELECT id, revision, data FROM reports WHERE owner_id = %s ORDER BY id", (t["id"],)) == before


def test_tablero_tolera_parametros_invalidos(browser, make_user):
    b = browser(make_user("RESPONSABLE"))
    for qs in ("desde=xx&hasta=2026-13-40", "tecnico=abc", "tecnico=999999999", "desde=2026-12-31&hasta=2026-01-01",
               "desde=2000-01-01&hasta=2026-01-01", "rango=9999", "tecnico=1%27%20OR%201=1"):
        r = b.get(f"/tablero?{qs}")
        assert r.status_code == 200, qs
    assert "se ajustó la fecha inicial" in b.get("/tablero?desde=2000-01-01&hasta=2026-01-01").get_data(as_text=True)


def test_metricas_ignoran_informes_eliminados(browser, make_user, q, app, app_conn):
    t = make_user("TECNICO")
    bt = browser(t)
    rid = create_report(bt)
    today = dt.date.today()
    with app.app_context():
        assert analytics.executive(today, today, t["id"], "UTC")["kpi"]["reports"] == 1
    admin = browser(make_user("RESPONSABLE"))
    assert admin.post(f"/informes/{rid}/eliminar", {"motivo": "Informe duplicado por error", "op_id": "b" * 32},
                      csrf_from=f"/informes/{rid}").status_code == 302
    with app.app_context():
        assert analytics.executive(today, today, t["id"], "UTC")["kpi"]["reports"] == 0
        assert analytics.activity_calendar(t["id"], "UTC")["total"] == 0
    assert json.loads(q("SELECT data FROM reports WHERE id = %s", (rid,))[0]["data"])  # el registro sigue ahí


def test_boton_firmar_coloca_la_firma_guardada_y_guardar_cambios_la_registra(browser, make_user, q):
    _t, _bt, rid = _closed_report(browser, make_user, q)
    director = make_user("RESPONSABLE")
    bd = browser(director)
    assert bd.get("/cuenta/perfil/firma.png").status_code == 404  # aún sin firma guardada
    _profile_signature(bd)
    page = bd.get(f"/informes/{rid}").get_data(as_text=True)
    assert 'data-act="profile" data-src="/cuenta/perfil/firma.png">Firmar</button>' in page
    assert 'id="sign-form"' in page and 'data-sign-save>Guardar cambios</button>' in page
    img = bd.get("/cuenta/perfil/firma.png")
    assert img.status_code == 200 and img.mimetype == "image/png" and "no-store" in img.headers["Cache-Control"]
    # «Guardar cambios» envía el formulario de firma: el servidor copia la firma guardada del Director.
    assert bd.post(f"/informes/{rid}/firmar", csrf_from=f"/informes/{rid}").status_code == 302
    saved = q("SELECT a.sha256 FROM report_attachments a WHERE a.report_id = %s AND a.kind = 'FIRMA_RECIBE' "
              "AND a.deleted_at IS NULL", (rid,))[0]["sha256"]
    assert saved == q("SELECT sha256 FROM user_signatures WHERE user_id = %s", (director["id"],))[0]["sha256"]
    # Un técnico no ve el botón en un informe cerrado.
    other = browser(make_user("TECNICO"))
    _profile_signature(other)
    assert 'data-act="profile"' not in other.get(f"/informes/{rid}").get_data(as_text=True)
