"""Cierre como último paso, reapertura de informes firmados y eliminación lógica de usuarios."""
import pymysql
import pytest
from conftest import create_report, fill, new_op

from test_director_paneles import _active_receive, _closed_report, _profile_signature


def _signed_report(browser, make_user, q):
    t, bt, rid = _closed_report(browser, make_user, q)
    director = make_user("RESPONSABLE")
    bd = browser(director)
    _profile_signature(bd)
    assert bd.post(f"/informes/{rid}/firmar", csrf_from=f"/informes/{rid}").status_code == 302
    assert len(_active_receive(q, rid)) == 1
    return t, bt, director, bd, rid


def _status(q, rid):
    return q("SELECT status_code FROM reports WHERE id = %s", (rid,))[0]["status_code"]


# ---------------------------------------------------------------- reapertura de informes firmados
def test_tecnico_no_reabre_un_informe_firmado_por_el_director(browser, make_user, q):
    _t, bt, _d, _bd, rid = _signed_report(browser, make_user, q)
    page = bt.get(f"/informes/{rid}").get_data(as_text=True)
    assert "Reabrir para editar" not in page and "Solo el Director puede reabrirlo" in page
    r = bt.post(f"/informes/{rid}/reabrir", {"motivo": "Quiero cambiar el informe firmado", "op_id": new_op()})
    assert r.status_code == 302 and _status(q, rid) == "CERRADO"
    assert len(_active_receive(q, rid)) == 1  # la firma del Director sigue vigente


def test_director_reabre_informe_firmado_y_la_firma_se_retira(browser, make_user, q):
    _t, bt, director, bd, rid = _signed_report(browser, make_user, q)
    r = bd.post(f"/informes/{rid}/reabrir", {"motivo": "Corregir el número de serie", "op_id": new_op()})
    assert r.status_code == 302 and _status(q, rid) == "REABIERTO"
    assert _active_receive(q, rid) == []
    retired = q("SELECT deleted_by FROM report_attachments WHERE report_id = %s AND kind = 'FIRMA_RECIBE'", (rid,))
    assert retired == [{"deleted_by": director["id"]}]  # retirada lógica: la firma anterior se conserva
    # Al volver a cerrarlo queda otra vez pendiente de la firma del Director.
    assert fill(bt, q, rid, accion="cerrar", nombre="Corregido").status_code == 302
    assert f"/informes/{rid}" in bd.get("/").get_data(as_text=True)


def test_tecnico_reabre_su_informe_cerrado_sin_firmar(browser, make_user, q):
    _t, bt, rid = _closed_report(browser, make_user, q)
    assert "Reabrir para editar" in bt.get(f"/informes/{rid}").get_data(as_text=True)
    r = bt.post(f"/informes/{rid}/reabrir", {"motivo": "Faltó anotar el disco", "op_id": new_op()})
    assert r.status_code == 302 and _status(q, rid) == "REABIERTO"


# ---------------------------------------------------------------- estados antiguos
def test_informe_enviado_antiguo_se_muestra_como_cerrado(browser, make_user, q):
    t, bt, rid = _closed_report(browser, make_user, q)
    q("UPDATE reports SET status_code = 'ENVIADO', first_sent_at = closed_at, last_sent_at = closed_at, "
      "last_sent_by = %s WHERE id = %s", (t["id"], rid))
    for b in (bt, browser(make_user("RESPONSABLE"))):
        html = b.get(f"/informes/{rid}").get_data(as_text=True)
        assert "Enviado" not in html and "Cerrado" in html
    home = browser(make_user("JEFE")).get("/").get_data(as_text=True)
    assert "Enviado" not in home and "Informes cerrados recientemente" in home


# ---------------------------------------------------------------- eliminación de usuarios
def _delete(b, user_id, confirm="ELIMINAR"):
    return b.post(f"/administracion/usuarios/{user_id}/eliminar", {"confirmar": confirm},
                  csrf_from="/administracion/usuarios")


def test_director_elimina_usuario_y_conserva_sus_informes(browser, make_user, q):
    t = make_user("TECNICO")
    bt = browser(t)
    rid = create_report(bt)
    fill(bt, q, rid, accion="cerrar", nombre="Equipo del técnico eliminado")
    director = make_user("RESPONSABLE")
    bd = browser(director)
    assert t["username"] in bd.get("/administracion/usuarios").get_data(as_text=True)

    assert _delete(bd, t["id"], confirm="no").status_code == 302  # sin confirmación no se elimina
    assert q("SELECT deleted_at FROM users WHERE id = %s", (t["id"],))[0]["deleted_at"] is None

    assert _delete(bd, t["id"]).status_code == 302
    row = q("SELECT is_active, deleted_at, deleted_by FROM users WHERE id = %s", (t["id"],))[0]
    assert row["is_active"] == 0 and row["deleted_at"] and row["deleted_by"] == director["id"]
    assert f"<code>{t['username']}</code>" not in bd.get("/administracion/usuarios").get_data(as_text=True)
    # Su sesión abierta se revoca y ya no puede iniciar sesión.
    assert bt.get("/").status_code == 302
    assert browser().login(t).status_code == 401
    assert q("SELECT COUNT(*) AS n FROM sessions WHERE user_id = %s AND ended_at IS NULL", (t["id"],))[0]["n"] == 0
    # Nada se pierde: el informe sigue a su nombre y visible para la dirección.
    assert q("SELECT owner_id, title FROM reports WHERE id = %s", (rid,))[0] == {
        "owner_id": t["id"], "title": "Equipo del técnico eliminado"}
    assert bd.get(f"/informes/{rid}").status_code == 200
    consolidation = bd.get(f"/consolidacion?tecnico={t['id']}").get_data(as_text=True)
    assert "1 informe(s)" in consolidation and "eliminado" in consolidation
    ev = q("SELECT detail FROM audit_log WHERE event_type = 'USUARIO_ELIMINADO' AND object_id = %s", (t["username"],))
    assert ev and '"informes_conservados": 1' in ev[0]["detail"]
    # Una cuenta eliminada no se puede reactivar ni modificar.
    bd.post(f"/administracion/usuarios/{t['id']}/estado", {"activo": "1"}, csrf_from="/administracion/usuarios")
    assert q("SELECT is_active FROM users WHERE id = %s", (t["id"],))[0]["is_active"] == 0


def test_director_no_se_elimina_a_si_mismo(browser, make_user, q):
    director = make_user("RESPONSABLE")
    bd = browser(director)
    assert _delete(bd, director["id"]).status_code == 302
    assert q("SELECT deleted_at FROM users WHERE id = %s", (director["id"],))[0]["deleted_at"] is None


def test_jefe_y_tecnico_no_eliminan_usuarios(browser, make_user, q):
    victim = make_user("TECNICO")
    for role in ("JEFE", "TECNICO"):
        assert _delete(browser(make_user(role)), victim["id"]).status_code == 403
    assert q("SELECT deleted_at FROM users WHERE id = %s", (victim["id"],))[0]["deleted_at"] is None


def test_base_de_datos_impide_reactivar_eliminado(make_user, q):
    t = make_user("TECNICO")
    director = make_user("RESPONSABLE")
    q("UPDATE users SET is_active = 0, deleted_at = UTC_TIMESTAMP(6), deleted_by = %s WHERE id = %s",
      (director["id"], t["id"]))
    with pytest.raises(pymysql.MySQLError):
        q("UPDATE users SET is_active = 1 WHERE id = %s", (t["id"],))
