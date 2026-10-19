"""Definición declarativa de los campos de un informe (plantilla de captura).

Los campos definitivos NO están en el código: se describen en report_templates/<codigo>.json.
Para integrar la plantilla oficial basta con añadir un JSON nuevo (y su plantilla HTML de
documento) y activarlo con AJS_REPORT_TEMPLATE; los informes existentes conservan la suya.

Tipos admitidos: text, textarea, date, integer, select, checkbox.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "report_templates"
_CODE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
FIELD_TYPES = {"text", "textarea", "date", "integer", "select", "checkbox"}
MAPPABLE_COLUMNS = {"title": 200, "equipment_serial": 100}


class TemplateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Field:
    id: str
    label: str
    type: str
    max_length: int
    required_on_close: bool
    maps_to: str | None
    options: tuple[str, ...]
    help: str


@dataclass(frozen=True)
class Template:
    code: str
    name: str
    provisional: bool
    notice: str
    document_template: str
    sections: tuple[tuple[str, str, tuple[Field, ...]], ...]

    @property
    def fields(self) -> list[Field]:
        return [f for _sid, _title, fields in self.sections for f in fields]


@lru_cache(maxsize=16)
def load_template(code: str) -> Template:
    if not _CODE_RE.match(code or ""):
        raise TemplateError("código de plantilla no válido")
    path = TEMPLATES_DIR / f"{code}.json"
    if not path.is_file():
        raise TemplateError(f"plantilla de informe '{code}' no encontrada")
    raw = json.loads(path.read_text(encoding="utf-8"))
    sections, seen, mapped = [], set(), set()
    for sec in raw["sections"]:
        fields = []
        for f in sec["fields"]:
            if f["type"] not in FIELD_TYPES:
                raise TemplateError(f"tipo de campo no admitido: {f['type']}")
            if f["id"] in seen or not _CODE_RE.match(f["id"]):
                raise TemplateError(f"identificador de campo inválido o repetido: {f['id']}")
            seen.add(f["id"])
            maps_to = f.get("maps_to")
            if maps_to is not None and (maps_to not in MAPPABLE_COLUMNS or maps_to in mapped):
                raise TemplateError(f"maps_to inválido: {maps_to}")
            if maps_to:
                mapped.add(maps_to)
            max_len = int(f.get("max_length", 1000))
            if maps_to:
                max_len = min(max_len, MAPPABLE_COLUMNS[maps_to])
            fields.append(Field(
                id=f["id"], label=f["label"], type=f["type"], max_length=max_len,
                required_on_close=bool(f.get("required_on_close", False)), maps_to=maps_to,
                options=tuple(f.get("options", [])), help=f.get("help", ""),
            ))
        sections.append((sec["id"], sec["title"], tuple(fields)))
    if raw["code"] != code:
        raise TemplateError("el código interno de la plantilla no coincide con el archivo")
    return Template(code=code, name=raw["name"], provisional=bool(raw.get("provisional")),
                    notice=raw.get("notice", ""), document_template=raw["document_template"],
                    sections=tuple(sections))


def parse_form(template: Template, form) -> tuple[dict, dict[str, str]]:
    """Convierte y valida el formulario. Devuelve (datos, errores_por_campo). Ignora claves desconocidas."""
    data, errors = {}, {}
    for f in template.fields:
        raw = form.get(f.id, "")
        if f.type == "checkbox":
            data[f.id] = raw in ("1", "on", "true")
            continue
        value = (raw or "").replace("\r\n", "\n").strip()
        if len(value) > f.max_length:
            errors[f.id] = f"Máximo {f.max_length} caracteres."
            continue
        if value == "":
            data[f.id] = None
            continue
        if f.type in ("text",) and "\n" in value:
            errors[f.id] = "No se admiten saltos de línea."
        elif f.type == "date":
            try:
                value = dt.date.fromisoformat(value).isoformat()
            except ValueError:
                errors[f.id] = "Fecha no válida (AAAA-MM-DD)."
        elif f.type == "integer":
            if not re.fullmatch(r"-?\d{1,12}", value):
                errors[f.id] = "Debe ser un número entero."
            else:
                value = int(value)
        elif f.type == "select" and value not in f.options:
            errors[f.id] = "Opción no válida."
        data[f.id] = value
    return data, errors


def missing_for_close(template: Template, data: dict) -> dict[str, str]:
    return {f.id: "Obligatorio para cerrar el informe." for f in template.fields
            if f.required_on_close and data.get(f.id) in (None, "", False)}


def mapped_columns(template: Template, data: dict) -> dict:
    cols = {"title": "", "equipment_serial": None}
    for f in template.fields:
        if f.maps_to:
            value = data.get(f.id)
            cols[f.maps_to] = (str(value) if value is not None else ("" if f.maps_to == "title" else None))
    return cols


def empty_data(template: Template) -> dict:
    return {f.id: (False if f.type == "checkbox" else None) for f in template.fields}
