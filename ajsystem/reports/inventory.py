"""Matriz de inventario: equipos reales que hay que auditar, convalidados contra los informes.

La matriz la carga el Director desde el tablero y se guarda en la base (el repositorio es público:
sus datos no deben estar en él). Cada carga es un lote nuevo y la vigente es la última, así que la
cuenta de la aplicación no necesita UPDATE ni DELETE. El cruce es de solo lectura: nunca modifica
ni elimina informes; los duplicados solo se señalan para que el Director decida.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

from .. import audit, db
from .importer import MAX_FILE_BYTES, ImportFileError, _rows_xlsx
from .service import CLOSED, SIGNED_SQL

# Encabezados de la matriz: se muestran tal cual en el tablero.
COL_NO, COL_SERIAL, COL_QTY, COL_FEATURES, COL_CATEGORY = (
    "N° Equipo", "N.º de serie", "Cantidad por dispositivo", "Características", "Categoría")
COLUMNS = (COL_NO, COL_SERIAL, COL_QTY, COL_FEATURES, COL_CATEGORY)
MAX_SERIAL, MAX_FEATURES, MAX_CATEGORY = 100, 2000, 120
MAX_REPORTS = 50000  # tope de seguridad, igual que el tablero ejecutivo

REGISTRADO, PARCIAL, DUPLICADO, PENDIENTE, SIN_SERIAL = "REGISTRADO", "PARCIAL", "DUPLICADO", "PENDIENTE", "SIN_SERIAL"
STATUS_LABELS = {REGISTRADO: "Registrado", PARCIAL: "Serial incompleto", DUPLICADO: "Duplicado",
                 PENDIENTE: "Pendiente", SIN_SERIAL: "Sin serial"}
WITH_REPORT = {REGISTRADO, PARCIAL, DUPLICADO}

# Por debajo de este largo un serial de la matriz se considera truncado y se compara por prefijo
# (los seriales completos más cortos, las etiquetas Dell, tienen 7 caracteres).
MIN_FULL_SERIAL = 7
MIN_PREFIX = 3
_NOT_ALNUM = re.compile(r"[^A-Z0-9]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NO_SERIAL = frozenset({"NA", "NO", "NOAPLICA", "NOTIENE", "NINGUNO", "SINSERIAL", "SINSERIE"})


def normalize_serial(value) -> str:
    """Clave de comparación: «SN PF-12 ab», «S/N PF12AB» y «pf12ab» son el mismo serial."""
    text = _NOT_ALNUM.sub("", str(value or "").upper())
    text = text.removeprefix("SN")
    return "" if text in _NO_SERIAL else text


def _header_key(value) -> str:
    return " ".join(str(value or "").split()).casefold()


def _text(value, limit: int, label: str, line: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = _CONTROL.sub("", str(value)).strip()
    if len(text) > limit:
        raise ImportFileError(f"Fila {line}: «{label}» supera {limit} caracteres.")
    return text or None


def _integer(value, label: str, line: int) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = -1.0
    if number < 0 or not number.is_integer() or number > 4_000_000_000:
        raise ImportFileError(f"Fila {line}: «{label}» debe ser un número entero.")
    return int(number)


def parse(raw: bytes) -> list[dict]:
    """Lee la matriz (.xlsx). Exige las columnas exactas; cualquier fila inválida rechaza la carga."""
    if not raw:
        raise ImportFileError("No se recibió ningún archivo.")
    if len(raw) > MAX_FILE_BYTES:
        raise ImportFileError("El archivo supera el tamaño máximo (5 MB).")
    if raw[:2] != b"PK":
        raise ImportFileError("Formato no admitido. Use la matriz en Excel (.xlsx).")
    rows = _rows_xlsx(raw)
    wanted = {_header_key(c): c for c in COLUMNS}
    for idx, row in enumerate(rows[:10]):
        positions = {wanted[_header_key(v)]: i for i, v in enumerate(row) if _header_key(v) in wanted}
        if len(positions) == len(COLUMNS):
            break
    else:
        raise ImportFileError("La matriz debe tener las columnas: " + ", ".join(COLUMNS) + ".")
    items, seen = [], set()
    for line, row in enumerate(rows[idx + 1:], start=idx + 2):
        cells = {c: (row[i] if i < len(row) else None) for c, i in positions.items()}
        if not any(str(v).strip() for v in cells.values() if v is not None):
            continue
        number = _integer(cells[COL_NO], COL_NO, line)
        if number is None:
            raise ImportFileError(f"Fila {line}: falta «{COL_NO}».")
        if number in seen:
            raise ImportFileError(f"Fila {line}: «{COL_NO}» {number} está repetido.")
        seen.add(number)
        items.append({"item_no": number, "serial": _text(cells[COL_SERIAL], MAX_SERIAL, COL_SERIAL, line),
                      "quantity": _integer(cells[COL_QTY], COL_QTY, line),
                      "characteristics": _text(cells[COL_FEATURES], MAX_FEATURES, COL_FEATURES, line),
                      "category": _text(cells[COL_CATEGORY], MAX_CATEGORY, COL_CATEGORY, line)})
    if not items:
        raise ImportFileError("La matriz no contiene equipos.")
    return items


def save(user: dict, raw: bytes, file_name: str) -> int:
    """Guarda la matriz como lote nuevo (pasa a ser la vigente). Todo o nada, en una transacción."""
    items = parse(raw)
    name = (file_name or "matriz.xlsx").replace("\\", "/").rsplit("/", 1)[-1][:190]
    digest = hashlib.sha256(raw).hexdigest()
    with db.transaction() as cur:
        cur.execute("""INSERT INTO inventory_imports (file_name, sha256, item_count, imported_by, imported_at)
                       VALUES (%s, %s, %s, %s, %s)""", (name, digest, len(items), user["id"], db.utcnow()))
        import_id = cur.lastrowid
        cur.executemany(
            """INSERT INTO inventory_items (import_id, item_no, serial, quantity, characteristics, category)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            [(import_id, i["item_no"], i["serial"], i["quantity"], i["characteristics"], i["category"]) for i in items])
        audit.write(cur, audit.INVENTORY_IMPORTED, "EXITO", object_type="inventario", object_id=name[:64],
                    detail={"carga": import_id, "equipos": len(items), "sha256": digest})
    return len(items)


def _reports() -> list[dict]:
    rows = db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.equipment_brand, r.status_code, r.created_at,
                   o.full_name AS owner_name, {SIGNED_SQL} AS signed
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE r.deleted_at IS NULL ORDER BY r.created_at, r.id LIMIT %s""", (MAX_REPORTS,))
    for r in rows:
        r["signed"] = bool(r["signed"]) and r["status_code"] in CLOSED
        r["serial_key"] = normalize_serial(r["equipment_serial"])
        r["delete_candidate"] = False
    return rows


def _keeper(group: list[dict]) -> dict:
    """El informe que se conserva: firmado, luego cerrado, luego el más reciente."""
    return max(group, key=lambda r: (r["signed"], r["status_code"] in CLOSED, r["created_at"], r["id"]))


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def reconcile() -> dict | None:
    """Cruza la matriz vigente con los informes no eliminados. None si aún no se ha cargado ninguna."""
    current = db.query_one(
        """SELECT i.id, i.file_name, i.item_count, i.imported_at, u.full_name AS imported_by_name
             FROM inventory_imports i JOIN users u ON u.id = i.imported_by ORDER BY i.id DESC LIMIT 1""")
    if current is None:
        return None
    items = db.query_all("""SELECT item_no, serial, quantity, characteristics, category FROM inventory_items
                             WHERE import_id = %s ORDER BY item_no""", (current["id"],))
    reports = _reports()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in reports:
        if r["serial_key"]:
            groups[r["serial_key"]].append(r)

    duplicates = []
    for key, group in groups.items():
        if len(group) > 1:
            keep = _keeper(group)
            others = [r for r in group if r is not keep]
            for r in others:
                r["delete_candidate"] = True
            duplicates.append({"serial": group[0]["equipment_serial"], "key": key, "keep": keep, "others": others,
                               "item_no": None})

    keys = Counter(k for k in (normalize_serial(i["serial"]) for i in items) if k)
    matched: dict[str, int] = {}
    for item in items:
        key = normalize_serial(item["serial"])
        found = []
        if key in groups:
            found = [key]
        elif MIN_PREFIX <= len(key) < MIN_FULL_SERIAL:
            found = sorted(k for k in groups if k.startswith(key) and k not in keys)
        item["reports"] = [r for k in found for r in groups[k]]
        item["ambiguous"] = len(found) > 1
        item["signed"] = any(r["signed"] for r in item["reports"])
        for k in found:
            matched.setdefault(k, item["item_no"])
        if not key:
            item["status"] = SIN_SERIAL
        elif not found:
            item["status"] = PENDIENTE
        elif any(len(groups[k]) > 1 for k in found):
            item["status"] = DUPLICADO
        else:
            item["status"] = PARCIAL if key not in groups else REGISTRADO
        item["status_label"] = STATUS_LABELS[item["status"]]
    for dup in duplicates:
        dup["item_no"] = matched.get(dup["key"])

    counts = Counter(i["status"] for i in items)
    with_serial = len(items) - counts[SIN_SERIAL]
    registered = sum(counts[s] for s in WITH_REPORT)
    categories = []
    by_category: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_category[item["category"] or "Sin categoría"].append(item)
    for label, group in sorted(by_category.items()):
        c = Counter(i["status"] for i in group)
        done = sum(c[s] for s in WITH_REPORT)
        base = len(group) - c[SIN_SERIAL]
        categories.append({"label": label, "items": len(group), "units": sum(i["quantity"] or 0 for i in group),
                           "registered": done, "pending": c[PENDIENTE], "no_serial": c[SIN_SERIAL],
                           "pct": _ratio(done, base)})
    return {
        "import": current, "items": items, "categories": categories, "duplicates": duplicates,
        "outside": [r for r in reports if r["serial_key"] and r["serial_key"] not in matched],
        "no_serial_reports": [r for r in reports if not r["serial_key"]],
        "repeated_in_matrix": sorted(k for k, n in keys.items() if n > 1),
        "truncated": len(reports) >= MAX_REPORTS,
        "kpi": {
            "items": len(items), "units": sum(i["quantity"] or 0 for i in items), "with_serial": with_serial,
            "registered": registered, "registered_pct": _ratio(registered, with_serial),
            "pending": counts[PENDIENTE], "no_serial": counts[SIN_SERIAL], "partial": counts[PARCIAL],
            "signed": sum(1 for i in items if i["signed"]),
            "duplicated_items": counts[DUPLICADO], "delete_candidates": sum(len(d["others"]) for d in duplicates),
        },
    }
