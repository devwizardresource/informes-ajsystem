"""Matriz de inventario: carga por el Director y convalidación por número de serie."""
from __future__ import annotations

import io
import uuid

import pytest
from openpyxl import Workbook

from ajsystem import analytics
from ajsystem.reports import importer, inventory
from conftest import create_report, fill, new_op

HEADERS = ["N° Equipo", "N.º de serie", "Cantidad por dispositivo", "Características", "Categoría"]


def xlsx(rows, headers=HEADERS) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def upload_matrix(b, raw, name="matriz.xlsx"):
    token = b.csrf("/tablero/convalidacion")
    return b.client.post("/tablero/convalidacion/matriz", content_type="multipart/form-data",
                         data={"csrf_token": token, "archivo": (io.BytesIO(raw), name)})


def tag() -> str:
    return uuid.uuid4().hex[:8].upper()


@pytest.mark.parametrize("value,expected", [
    ("SN PF12AB34", "PF12AB34"), ("S/N PF12-ab34", "PF12AB34"), (" pf12ab34 ", "PF12AB34"), ("SN4X1Y", "4X1Y"),
    ("N/A", ""), ("sin serial", ""), ("SN", ""), (None, ""), ("", ""),
])
def test_normaliza_seriales(value, expected):
    assert inventory.normalize_serial(value) == expected


def test_solo_el_director_carga_la_matriz(browser, make_user):
    for role in ("TECNICO", "JEFE"):
        b = browser(make_user(role))
        page = b.get("/tablero/convalidacion")
        assert page.status_code == 200 and "Cargar matriz" not in page.get_data(as_text=True)
        assert b.post("/tablero/convalidacion/matriz", csrf_from="/").status_code == 403
    assert browser().get("/tablero/convalidacion").status_code == 302


def test_carga_exige_csrf(browser, make_user, q):
    b = browser(make_user("RESPONSABLE"))
    before = q("SELECT COUNT(*) AS n FROM inventory_imports")[0]["n"]
    r = b.client.post("/tablero/convalidacion/matriz", content_type="multipart/form-data",
                      data={"archivo": (io.BytesIO(xlsx([[1, "X", 1, "", ""]])), "m.xlsx")})
    assert r.status_code in (400, 403)
    assert q("SELECT COUNT(*) AS n FROM inventory_imports")[0]["n"] == before


@pytest.mark.parametrize("raw,message", [
    (b"no es excel", "Formato no admitido"),
    (b"", "No se recibió"),
    (xlsx([[1, "A", 1, "", ""]], ["Equipo", "Serie", "Cantidad", "Detalle", "Tipo"]), "debe tener las columnas"),
    (xlsx([[1, "A", 1, "", ""], [1, "B", 1, "", ""]]), "repetido"),
    (xlsx([["x", "A", 1, "", ""]]), "número entero"),
    (xlsx([[None, "A", 1, "", ""]]), "falta"),
    (xlsx([[1, "A", "uno", "", ""]]), "número entero"),
    (xlsx([[1, "A" * 101, 1, "", ""]]), "supera"),
    (xlsx([]), "no contiene equipos"),
])
def test_rechaza_matrices_invalidas(raw, message):
    with pytest.raises(importer.ImportFileError, match=message):
        inventory.parse(raw)


def test_lee_la_matriz_e_ignora_filas_vacias():
    items = inventory.parse(xlsx([[1, "SN ABC1234", 1, "Portátil\n16 GB", "Contrato#1"], [None] * 5,
                                  [2, None, 12, "12 teclados", "Contrato#2"]]))
    assert [i["item_no"] for i in items] == [1, 2]
    assert items[0]["characteristics"] == "Portátil\n16 GB" and items[1]["serial"] is None and items[1]["quantity"] == 12


def test_convalidacion_completa(app, browser, make_user, q):
    x = tag()
    exact, spaced, dup, truncated, pending, outside = (f"{p}{x}" for p in ("EX", "SP", "DU", "TR", "PE", "OU"))
    t, director = make_user("TECNICO"), make_user("RESPONSABLE")
    bt, bd = browser(t), browser(director)

    def report(serial, **kw):
        rid = create_report(bt)
        assert fill(bt, q, rid, numero_serie=serial, **kw).status_code == 302
        return rid

    r_exact = report(exact)
    r_spaced = report(f"sn-{spaced.lower()}")
    r_dup_closed = report(dup, accion="cerrar")
    r_dup_open = report(dup)
    r_trunc = report(truncated + "99")
    r_out = report(outside)
    r_deleted = report(pending)
    assert bd.post(f"/informes/{r_deleted}/eliminar", {"motivo": "Prueba de eliminación", "op_id": new_op()},
                   csrf_from="/tablero/convalidacion").status_code == 302

    short = truncated[:5]
    r_trunc2 = report(short + "ZZZZ")
    matrix = [[1, f"SN {exact}", 1, "<script>alert(1)</script>", "Contrato#1"], [2, spaced, 1, "Portátil", "Contrato#1"],
              [3, dup, 1, "Portátil", "Contrato#2"], [4, f"SN{short}", 1, "Portátil", "Contrato#2"],
              [5, pending, 1, "Portátil", "Otros"], [6, None, 12, "12 teclados", "Otros"]]
    r = upload_matrix(bd, xlsx(matrix), "../<b>matriz.xlsx")
    assert r.status_code == 302

    with app.app_context():
        m = inventory.reconcile()
        by = {i["item_no"]: i for i in m["items"]}
        assert [by[n]["status"] for n in range(1, 7)] == [
            inventory.REGISTRADO, inventory.REGISTRADO, inventory.DUPLICADO, inventory.PARCIAL,
            inventory.PENDIENTE, inventory.SIN_SERIAL]
        assert [r_["id"] for r_ in by[1]["reports"]] == [r_exact] and [r_["id"] for r_ in by[2]["reports"]] == [r_spaced]
        assert {r_["id"] for r_ in by[4]["reports"]} == {r_trunc, r_trunc2} and by[4]["ambiguous"]
        k = m["kpi"]
        assert k["items"] == 6 and k["units"] == 17 and k["with_serial"] == 5 and k["registered"] == 4
        assert k["pending"] == 1 and k["no_serial"] == 1 and k["partial"] == 1 and k["registered_pct"] == 4 / 5
        dup_group = next(d for d in m["duplicates"] if d["key"] == inventory.normalize_serial(dup))
        assert dup_group["keep"]["id"] == r_dup_closed and [o["id"] for o in dup_group["others"]] == [r_dup_open]
        assert dup_group["item_no"] == 3
        assert r_out in {o["id"] for o in m["outside"]} and r_deleted not in {o["id"] for o in m["outside"]}
        assert {c["label"]: c["registered"] for c in m["categories"]} == {"Contrato#1": 2, "Contrato#2": 2, "Otros": 0}

    page = bd.get("/tablero/convalidacion").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    for header in HEADERS:
        assert header in page
    assert "Posible eliminación" in page and "<b>matriz" not in page
    filtered = bd.get("/tablero/convalidacion?estado=PENDIENTE").get_data(as_text=True)
    assert pending in filtered and f"SN {exact}" not in filtered
    assert f"SN {exact}" in bd.get(f"/tablero/convalidacion?buscar={exact.lower()}").get_data(as_text=True)
    assert "Ningún equipo coincide" in bd.get("/tablero/convalidacion?buscar=no-existe-nada").get_data(as_text=True)
    assert bd.get("/tablero/convalidacion?estado=%27OR1&categoria=%3Cx%3E").status_code == 200
    assert "Avance frente a la matriz" in bd.get("/tablero").get_data(as_text=True)


def test_cruce_no_modifica_informes_y_la_ultima_carga_es_la_vigente(app, browser, make_user, q):
    d = make_user("RESPONSABLE")
    b = browser(d)
    before = q("SELECT id, revision, status_code, deleted_at FROM reports ORDER BY id")
    first, second = tag(), tag()
    assert upload_matrix(b, xlsx([[1, first, 1, "", ""]])).status_code == 302
    assert upload_matrix(b, xlsx([[1, second, 1, "", ""], [2, tag(), 1, "", ""]])).status_code == 302
    b.get("/tablero/convalidacion")
    assert q("SELECT id, revision, status_code, deleted_at FROM reports ORDER BY id") == before
    with app.app_context():
        assert [i["serial"] for i in inventory.reconcile()["items"]][0] == second
    assert q("SELECT COUNT(*) AS n FROM inventory_items WHERE serial = %s", (first,))[0]["n"] == 1  # historial
    events = q("""SELECT outcome FROM audit_log WHERE event_type = 'MATRIZ_INVENTARIO_CARGADA' AND username = %s""",
               (d["username"],))
    assert [e["outcome"] for e in events] == ["EXITO", "EXITO"]


def test_carga_fallida_conserva_la_matriz_vigente_y_se_audita(app, browser, make_user, q):
    d = make_user("RESPONSABLE")
    b = browser(d)
    serial = tag()
    upload_matrix(b, xlsx([[1, serial, 1, "", ""]]))
    r = upload_matrix(b, xlsx([[1, "A", 1, "", ""]], ["a", "b", "c", "d", "e"]))
    assert r.status_code == 302
    with app.app_context():
        assert inventory.reconcile()["items"][0]["serial"] == serial
    assert q("""SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'MATRIZ_INVENTARIO_CARGADA'
                  AND outcome = 'FALLO' AND username = %s""", (d["username"],))[0]["n"] == 1


def test_la_aplicacion_no_puede_borrar_ni_cambiar_la_matriz(app_conn):
    with app_conn.cursor() as cur:
        for sql in ("DELETE FROM inventory_items", "UPDATE inventory_items SET serial = 'x'",
                    "DELETE FROM inventory_imports", "UPDATE inventory_imports SET file_name = 'x'"):
            with pytest.raises(Exception, match="denied"):
                cur.execute(sql)


def test_tablero_cuenta_un_equipo_aunque_el_serial_se_escriba_distinto(app, browser, make_user, q):
    import datetime as dt
    t = make_user("TECNICO")
    bt = browser(t)
    serial = tag()
    for value, brand in ((serial, "Dell"), (f"SN {serial.lower()}", "DELL INC")):
        fill(bt, q, create_report(bt), numero_serie=value, marca=brand)
    today = dt.date.today()
    with app.app_context():
        k = analytics.executive(today - dt.timedelta(days=1), today + dt.timedelta(days=1), t["id"], "UTC")["kpi"]
    assert k["reports"] == 2 and k["equipment"] == 1 and k["duplicates"] == 1


def test_tablero_sigue_funcionando_si_falla_la_matriz(browser, make_user, monkeypatch):
    import pymysql
    b = browser(make_user("RESPONSABLE"))

    def boom():
        raise pymysql.ProgrammingError(1146, "tabla inexistente")
    monkeypatch.setattr(inventory, "reconcile", boom)
    assert "No fue posible consultar la matriz" in b.get("/tablero").get_data(as_text=True)
    assert "No fue posible consultar la matriz" in b.get("/tablero/convalidacion").get_data(as_text=True)
