"""Tipos de dispositivo, dictamen de auditoría, tablero de portátiles y consulta sin modificación."""
from __future__ import annotations

import uuid

import pytest

from ajsystem.reports import inventory, verdict
from conftest import create_report, fill
from test_convalidacion_inventario import upload_matrix, xlsx


@pytest.mark.parametrize("text,expected", [
    ("DELL INSPIRON, GENERACION 13 - 15 PULGADAS CORE i5 WDS PRO 11 512 GB.", inventory.PORTATIL),
    ("DELL LATITUDE, CORE I5. SSD 512, DDR4 16GB WINDOWS 11 PRO. PANTALLA 14,5,", inventory.PORTATIL),
    ("LENOVO Procesador Intel Core i5-1235U RAM 16 GB", inventory.PORTATIL),
    ("ASUS FX516P MODELO FX516PE", inventory.PORTATIL),
    ("ASUS CORPORATIVO CORI I5, 512, 14 PULGADAS.", inventory.PORTATIL),
    ("PORTATIL HP COREI 5 13 GENERACION MAQUINA DE REPUESTO", inventory.PORTATIL),
    ('MONITOR 22U401A-B 22 Pulgadas FULL HD AMD FreeSync', inventory.MONITOR),
    ("MONITOR AJHUA PANTALLA LED, 22 PULGADAS", inventory.MONITOR),
    ("SAMSUNG S22C310EAL/ 22 Pulgadas/ Full HD/ 75 Hz/ IPS/ AMD FreeSync", inventory.MONITOR),
    ("12 TECLADOS Y MOUSE MARCA GENERICA", inventory.PERIFERICO),
    ("CONECTOR TIPO C A PUERTO RJ45", inventory.PERIFERICO),
    ("Equipo desconocido", inventory.SIN_CLASIFICAR),
    (None, inventory.SIN_CLASIFICAR),
])
def test_tipo_de_dispositivo_sale_de_la_matriz(text, expected):
    assert inventory.device_type(text) == expected


def _obs(text, estado="Activo"):
    return verdict.assess({"estado": estado, "observaciones": text})["code"]


@pytest.mark.parametrize("text,expected", [
    ("Se realiza limpieza exterior", verdict.OPTIMO),
    ("RAYON EN LA TAPA PRINCIPAL", verdict.OPTIMO),
    ("Sin daños ni golpes", verdict.OPTIMO),
    ("No presenta fallas", verdict.OPTIMO),
    ("Pantalla sin rayones ni fisuras", verdict.OPTIMO),
    ("Disco dañado pero reemplazado", verdict.OPTIMO),
    ("Bisagra derecha rota", verdict.OBSERVACIONES),
    ("Batería no retiene carga", verdict.OBSERVACIONES),
    ("Puerto USB no funciona", verdict.OBSERVACIONES),
    ("Requiere cambio de teclado", verdict.OBSERVACIONES),
    ("Algo dañado", verdict.OBSERVACIONES),
    ("No enciende", verdict.NO_APTO),
    ("No se pudo encender", verdict.NO_APTO),
    ("Sin rayones pero con pantalla rota", verdict.NO_APTO),
    ("La pantalla está totalmente quebrada", verdict.NO_APTO),
    ("Disco duro dañado", verdict.NO_APTO),
    ("Batería hinchada", verdict.NO_APTO),
    ("Equipo no funciona", verdict.NO_APTO),
])
def test_dictamen_lee_las_observaciones(text, expected):
    assert _obs(text) == expected


def test_dictamen_no_concluye_sin_evidencia_y_respeta_el_estado():
    assert verdict.assess({"estado": "Activo"})["code"] == verdict.SIN_EVIDENCIA
    assert _obs("Sin observaciones") == verdict.SIN_EVIDENCIA
    assert verdict.assess({"disco": "SSD 512 en buen estado"})["code"] == verdict.SIN_EVIDENCIA
    assert verdict.assess({"estado": "En reparación"})["code"] == verdict.NO_APTO
    assert verdict.assess({"estado": "Obsoleto", "disco": "bien"})["code"] == verdict.NO_APTO
    assert verdict.assess({"estado": "Activo", "ram": "Mala"})["code"] == verdict.NO_APTO
    excluded = {"estado": "Activo", "disco": "bien", "observaciones": "pantalla rota", "observaciones_habilitadas": False}
    assert verdict.assess(excluded)["code"] == verdict.OPTIMO


def _serial() -> str:
    return "T" + uuid.uuid4().hex[:11].upper()


@pytest.fixture()
def scenario(browser, make_user, q, app):
    director, tech, other = make_user("RESPONSABLE"), make_user("TECNICO"), make_user("TECNICO")
    bd, bt, bo = browser(director), browser(tech), browser(other)
    good, bad, marked, draft, pending, monitor, outside = (_serial() for _ in range(7))

    def report(b, serial, **kw):
        rid = create_report(b)
        assert fill(b, q, rid, numero_serie=serial, **kw).status_code == 302
        return rid

    ok_id = report(bt, good, accion="cerrar", observaciones_habilitadas="1", observaciones="Rayón leve en la tapa")
    bad_id = report(bo, bad, accion="cerrar", observaciones_habilitadas="1",
                    observaciones="<script>alert(1)</script> no enciende")
    marked_id = report(bt, marked, accion="cerrar", observaciones_habilitadas="1", observaciones="Bisagra floja")
    report(bt, draft)
    report(bt, monitor, accion="cerrar", observaciones_habilitadas="1", observaciones="pantalla rota")
    outside_id = report(bt, outside, accion="cerrar", observaciones_habilitadas="1", observaciones="No enciende")
    laptop = "DELL INSPIRON 15 PULGADAS CORE i5"
    matrix = [[1, good, 1, laptop, "Contrato#1"], [2, bad, 1, laptop, "Contrato#1"], [3, marked, 1, laptop, "Contrato#2"],
              [4, draft, 1, laptop, "Contrato#2"], [5, pending, 1, laptop, "Contrato#2"], [6, None, 1, "PORTATIL HP REPUESTO", "Contrato#2"],
              [7, monitor, 1, "MONITOR 22 PULGADAS", "Otros"], [8, None, 12, "12 TECLADOS Y MOUSE", "Otros"]]
    assert upload_matrix(bd, xlsx(matrix)).status_code == 302
    return {"bd": bd, "bt": bt, "bo": bo, "ok_id": ok_id, "bad_id": bad_id, "marked_id": marked_id,
            "outside": outside_id, "outside_serial": outside, "good": good, "tech": tech}


def test_cifras_por_tipo_y_dictamen_son_exactas(scenario, app):
    with app.app_context():
        m = inventory.reconcile()
        devices = {d["code"]: d for d in m["devices"]}
        assert devices[inventory.PORTATIL]["items"] == 6 and devices[inventory.PORTATIL]["units"] == 6
        assert devices[inventory.MONITOR]["items"] == 1 and devices[inventory.PERIFERICO]["units"] == 12
        assert sum(d["items"] for d in m["devices"]) == m["kpi"]["items"]
        a = inventory.laptop_audit(m)
    k = a["kpi"]
    assert (k["total"], k["with_serial"], k["issued"], k["optimal"], k["observed"], k["unfit"]) == (6, 5, 3, 1, 1, 1)
    assert (k["in_progress"], k["pending"], k["no_serial"], k["fit"]) == (1, 1, 1, 2)
    assert k["coverage"] == 3 / 5 and k["fit_pct"] == 2 / 3
    assert sum(v["n"] for v in a["verdicts"]) == k["total"]
    assert {i["device"] for i in a["items"]} == {inventory.PORTATIL}
    assert scenario["outside"] not in {i["report"]["id"] for i in a["items"] if i["report"]}


def test_informe_fuera_de_la_matriz_queda_igual(scenario, q, app):
    with app.app_context():
        m = inventory.reconcile()
    assert scenario["outside"] in {r["id"] for r in m["outside"]}
    row = q("SELECT status_code, deleted_at FROM reports WHERE id = %s", (scenario["outside"],))[0]
    assert row["status_code"] == "CERRADO" and row["deleted_at"] is None


def test_panel_de_portatiles_muestra_y_escapa(scenario):
    page = scenario["bd"].get("/tablero/portatiles").get_data(as_text=True)
    assert "Resumen ejecutivo" in page and "No enciende o no da imagen" in page
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)" in page
    assert "MONITOR 22" not in page and scenario["outside_serial"] not in page


def test_filtros_del_panel_toleran_valores_invalidos(scenario):
    b = scenario["bt"]
    for qs in ("dictamen=%27OR1", "categoria=%3Cx%3E", "tecnico=abc", "tecnico=999999999", "buscar=" + "x" * 500):
        assert b.get(f"/tablero/portatiles?{qs}").status_code == 200
    filtered = b.get("/tablero/portatiles?dictamen=NO_APTO").get_data(as_text=True)
    assert "Detalle por portátil <span class=\"count-pill\">1</span>" in filtered


def test_tecnico_consulta_sin_modificar_ni_abrir_informes_ajenos(scenario):
    b = scenario["bt"]
    for path in ("/tablero", "/tablero/convalidacion", "/tablero/portatiles"):
        page = b.get(path)
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert "Cargar matriz" not in html and 'name="archivo"' not in html
    html = b.get("/tablero/portatiles").get_data(as_text=True)
    assert f'/informes/{scenario["ok_id"]}"' in html  # propio: enlazado
    assert f'/informes/{scenario["bad_id"]}"' not in html  # ajeno: solo el código
    assert b.get(f'/informes/{scenario["bad_id"]}').status_code == 404
    assert b.post("/tablero/convalidacion/matriz", csrf_from="/tablero/portatiles").status_code == 403
    assert scenario["bd"].get(f'/informes/{scenario["bad_id"]}').status_code == 200


def test_sin_sesion_no_hay_panel(browser):
    assert browser().get("/tablero/portatiles").status_code == 302


def test_panel_sin_matriz_no_falla(browser, make_user, monkeypatch):
    monkeypatch.setattr(inventory, "reconcile", lambda: None)
    page = browser(make_user("TECNICO")).get("/tablero/portatiles")
    assert page.status_code == 200 and "Aún no se ha cargado" in page.get_data(as_text=True)
