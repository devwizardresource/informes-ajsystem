"""Generación documental (HTML → PDF en el servidor).

Contrato entre datos y plantilla (ver docs/PLANTILLA_PDF.md): build_document_data() produce un
diccionario estable con 'meta', 'secciones' e 'historial'. La plantilla HTML del documento
(document_templates/<archivo>.html) solo consume ese diccionario, así que la plantilla oficial
puede sustituir a la provisional sin tocar la lógica de negocio.

Motor PDF: WeasyPrint (Python, BSD). Se importa de forma diferida para que un fallo del motor
(p. ej. falta de Pango en el alojamiento) no impida el resto de la aplicación.
"""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .reports import schema
from .reports.service import STATUS_LABELS, _content_hash

DOC_TEMPLATES_DIR = Path(__file__).resolve().parent / "document_templates"
_env = Environment(loader=FileSystemLoader(str(DOC_TEMPLATES_DIR)), autoescape=select_autoescape(["html"]))
ACTION_LABELS = {"CREAR": "Creación", "GUARDAR": "Guardado", "CERRAR": "Cierre", "ENVIAR": "Envío", "REABRIR": "Reapertura"}


class PdfEngineUnavailable(RuntimeError):
    pass


def _fmt(value, tz: ZoneInfo) -> str:
    if value is None:
        return ""
    return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _display(field: schema.Field, value) -> str:
    if field.type == "checkbox":
        return "Sí" if value else "No"
    return "" if value is None else str(value)


def build_document_data(report: dict, events: list[dict], generated_by: dict, now, tz_name: str) -> dict:
    tz = ZoneInfo(tz_name)
    template = schema.load_template(report["template_code"])
    return {
        "plantilla": {"codigo": template.code, "nombre": template.name, "provisional": template.provisional},
        "meta": {
            "codigo": report["code"],
            "estado": STATUS_LABELS.get(report["status_code"], report["status_code"]),
            "revision": report["revision"],
            "autor": report["owner_name"],
            "creado": _fmt(report["created_at"], tz),
            "cerrado": _fmt(report["closed_at"], tz),
            "cerrado_por": report.get("closed_by_name") or "",
            "enviado": _fmt(report["last_sent_at"], tz),
            "enviado_por": report.get("sent_by_name") or "",
            "reaperturas": report["reopen_count"],
            "huella": _content_hash(report["title"], report["equipment_serial"], report["data"]),
            "generado": _fmt(now, tz),
            "generado_por": generated_by["full_name"],
            "zona_horaria": tz_name,
        },
        "secciones": [
            {"titulo": title, "campos": [{"id": f.id, "etiqueta": f.label, "tipo": f.type,
                                          "valor": _display(f, report["data"].get(f.id))} for f in fields]}
            for _sid, title, fields in template.sections
        ],
        "historial": [
            {"accion": ACTION_LABELS.get(e["action"], e["action"]), "fecha": _fmt(e["created_at"], tz),
             "usuario": e["full_name"], "estado": STATUS_LABELS.get(e["to_status"], e["to_status"]),
             "revision": e["revision"], "motivo": e["reason"] or ""}
            for e in events if e["action"] != "GUARDAR"
        ],
    }


def render_html(doc: dict, document_template: str) -> str:
    return _env.get_template(document_template).render(doc=doc)


def render_pdf(doc: dict, document_template: str) -> bytes:
    try:
        from weasyprint import HTML  # noqa: PLC0415 (import diferido intencional)
    except Exception as exc:  # OSError si faltan bibliotecas del sistema
        raise PdfEngineUnavailable(str(exc)) from exc
    html = render_html(doc, document_template)
    # base_url restringido a la carpeta de plantillas: no se cargan recursos remotos.
    return HTML(string=html, base_url=str(DOC_TEMPLATES_DIR), url_fetcher=_local_only_fetcher).write_pdf()


def _local_only_fetcher(url: str, *args, **kwargs):
    from weasyprint.urls import default_url_fetcher  # noqa: PLC0415
    if not url.startswith("file://" + str(DOC_TEMPLATES_DIR)):
        raise ValueError("recurso externo bloqueado en la generación de PDF")
    return default_url_fetcher(url, *args, **kwargs)
