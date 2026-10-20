"""Definición declarativa de los campos de un informe (plantilla de captura).

Los campos definitivos NO están en el código: se describen en report_templates/<codigo>.json.
Para integrar la plantilla oficial basta con añadir un JSON nuevo (y su plantilla HTML de
documento) y activarlo con AJS_REPORT_TEMPLATE; los informes existentes conservan la suya.

Tipos admitidos: text, textarea, date, integer, select, checkbox. Un select puede mostrarse
como casillas excluyentes ("widget": "radio"), igual que el formato impreso.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import NamedTuple
from functools import lru_cache
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "report_templates"
_CODE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
FIELD_TYPES = {"text", "textarea", "date", "integer", "select", "checkbox"}
MAPPABLE_COLUMNS = {"title": 200, "equipment_serial": 100, "equipment_brand": 100}
WIDGETS = {"", "radio"}
OFFICIAL_TEMPLATE_CODE = "aj_v1"


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
    widget: str = ""
    placeholder: str = ""
    default: str | None = None


class Section(NamedTuple):
    id: str
    title: str
    fields: tuple[Field, ...]
    number: int | None = None


@dataclass(frozen=True)
class Template:
    code: str
    name: str
    provisional: bool
    notice: str
    document_template: str
    sections: tuple[Section, ...]

    @property
    def fields(self) -> list[Field]:
        return [f for sec in self.sections for f in sec.fields]

    def field(self, field_id: str) -> Field | None:
        return next((f for f in self.fields if f.id == field_id), None)


def effective_template(code: str) -> Template:
    """Return the template used by the current UI and document renderer.

    Reports created before the owner delivered the official format keep their original
    ``template_code`` for auditability.  They are nevertheless edited and rendered with
    the official sheet so opening an old, still valid report cannot crash the application.
    """
    template = load_template(code)
    return load_template(OFFICIAL_TEMPLATE_CODE) if template.provisional else template


def data_for_template(code: str, data: dict | None) -> dict:
    """Adapt data from a legacy provisional report to the official field ids.

    The original dictionary is never mutated. Unknown legacy fields are deliberately kept
    by callers when saving so historical values remain in the JSON payload.
    """
    source = dict(data or {})
    original = load_template(code)
    if not original.provisional:
        if source.get("observaciones") and "observaciones_habilitadas" not in source:
            source["observaciones_habilitadas"] = True
        return source
    target = empty_data(effective_template(code))
    # Names used by provisional_v0. Official ``nombre`` and ``numero_serie`` are the
    # corresponding inventory fields in the delivered format.
    if source.get("titulo") not in (None, ""):
        target["nombre"] = source["titulo"]
    if source.get("numero_serie") not in (None, ""):
        target["numero_serie"] = source["numero_serie"]
    # If a report has already been submitted through the compatibility UI, prefer the
    # official values that are already present in its JSON payload.
    for field_id in target:
        if field_id in source:
            target[field_id] = source[field_id]
    if target.get("observaciones") and "observaciones_habilitadas" not in source:
        target["observaciones_habilitadas"] = True
    return target


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
            if f.get("widget", "") not in WIDGETS:
                raise TemplateError(f"widget no admitido: {f.get('widget')}")
            max_len = int(f.get("max_length", 1000))
            if maps_to:
                max_len = min(max_len, MAPPABLE_COLUMNS[maps_to])
            fields.append(Field(
                id=f["id"], label=f["label"], type=f["type"], max_length=max_len,
                required_on_close=bool(f.get("required_on_close", False)), maps_to=maps_to,
                options=tuple(f.get("options", [])), help=f.get("help", ""),
                widget=f.get("widget", ""), placeholder=f.get("placeholder", ""), default=f.get("default"),
            ))
        sections.append(Section(sec["id"], sec.get("title", ""), tuple(fields), sec.get("number")))
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
    if "observaciones_habilitadas" in data and not data["observaciones_habilitadas"]:
        data["observaciones"] = None
    return data, errors


def missing_for_close(template: Template, data: dict) -> dict[str, str]:
    return {f.id: "Obligatorio para cerrar el informe." for f in template.fields
            if f.required_on_close and data.get(f.id) in (None, "", False)}


def mapped_columns(template: Template, data: dict) -> dict:
    cols = {"title": "", "equipment_serial": None, "equipment_brand": None}
    for f in template.fields:
        if f.maps_to:
            value = data.get(f.id)
            cols[f.maps_to] = (str(value) if value is not None else ("" if f.maps_to == "title" else None))
    return cols


def empty_data(template: Template, today: dt.date | None = None) -> dict:
    data = {f.id: (False if f.type == "checkbox" else None) for f in template.fields}
    for f in template.fields:
        if f.type == "date" and f.default == "today" and today is not None:
            data[f.id] = today.isoformat()
    return data


def clean_value(field: Field, value) -> str | None:
    """Normaliza un valor sugerido (OCR o Excel) para el campo; None si no es aplicable."""
    if value is None:
        return None
    text = " ".join(str(value).split()) if field.type != "textarea" else str(value).strip()
    if not text:
        return None
    if field.type == "date":
        try:
            return dt.date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return None
    if field.type == "select":
        match = next((o for o in field.options if o.lower() == text.lower()), None)
        return match
    if field.type in ("checkbox", "integer"):
        return None
    return text[: field.max_length]
