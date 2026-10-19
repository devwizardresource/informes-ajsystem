"""Importación de una matriz Excel (.xlsx) o CSV: un informe por equipo (fila).

Defensas: tamaño máximo del archivo, límite de filas y columnas, comprobación del tamaño
descomprimido del .xlsx (bombas zip) y lectura con openpyxl en modo solo lectura y sin fórmulas
(data_only). openpyxl usa defusedxml, que bloquea entidades XML externas.
"""
from __future__ import annotations

import csv
import io
import zipfile

from . import extract, schema

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_UNZIPPED_BYTES = 60 * 1024 * 1024
MAX_ROWS = 500
MAX_COLS = 80


class ImportFileError(ValueError):
    pass


def _rows_xlsx(raw: bytes) -> list[list]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_BYTES:
                raise ImportFileError("El archivo Excel es demasiado grande al descomprimirlo.")
    except zipfile.BadZipFile as exc:
        raise ImportFileError("El archivo no es un Excel (.xlsx) válido.") from exc
    from openpyxl import load_workbook  # noqa: PLC0415 (import diferido: solo al importar)
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ImportFileError("No se pudo leer el archivo Excel.") from exc
    try:
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows(max_col=MAX_COLS, values_only=True):
            rows.append(list(row))
            if len(rows) > MAX_ROWS + 1:
                raise ImportFileError(f"La matriz supera el máximo de {MAX_ROWS} equipos por importación.")
        return rows
    finally:
        wb.close()


def _rows_csv(raw: bytes) -> list[list]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(io.StringIO(text), dialect):
        rows.append(row[:MAX_COLS])
        if len(rows) > MAX_ROWS + 1:
            raise ImportFileError(f"La matriz supera el máximo de {MAX_ROWS} equipos por importación.")
    return rows


def parse(raw: bytes, filename: str, template: schema.Template) -> tuple[list[dict], list[str]]:
    """Devuelve (filas_con_datos, columnas_reconocidas)."""
    if not raw:
        raise ImportFileError("No se recibió ningún archivo.")
    if len(raw) > MAX_FILE_BYTES:
        raise ImportFileError("El archivo supera el tamaño máximo (5 MB).")
    name = (filename or "").lower()
    if name.endswith(".xlsx") or raw[:2] == b"PK":
        rows = _rows_xlsx(raw)
    elif name.endswith((".csv", ".txt")):
        rows = _rows_csv(raw)
    else:
        raise ImportFileError("Formato no admitido. Use Excel (.xlsx) o CSV.")
    # La fila de encabezados es la primera con al menos dos columnas reconocidas.
    for idx, row in enumerate(rows[:10]):
        mapping = extract.header_map(row, template)
        if len(mapping) >= 2:
            break
    else:
        raise ImportFileError("No se reconocieron los encabezados. Descargue la plantilla de importación y úsela "
                           "como guía (por ejemplo: Nombre, Dispositivo, Marca, Número de serie, Modelo).")
    out = []
    for row in rows[idx + 1:]:
        if not any(str(c).strip() for c in row if c is not None):
            continue
        data = extract.from_row(row, mapping, template)
        if data:
            out.append(data)
    if not out:
        raise ImportFileError("La matriz no contiene filas con datos.")
    labels = [template.field(fid).label.rstrip(":") for fid in mapping.values()]
    return out, labels


def template_workbook(template: schema.Template) -> bytes:
    """Plantilla .xlsx con los encabezados exactos del formato de informe."""
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Font, PatternFill  # noqa: PLC0415
    wb = Workbook()
    ws = wb.active
    ws.title = "Equipos"
    headers = [f.label.rstrip(":") for f in template.fields]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B1020")
    ws.append(["", "PC-CONTABILIDAD-01", "Bogotá", "Calle 00 # 00-00, piso 2", "Portátil", "Dell", "7HG2K53",
               "Latitude 5410", "Contabilidad", "Nombre del responsable", "Activo"])
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(14, min(42, len(h) + 4))
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
