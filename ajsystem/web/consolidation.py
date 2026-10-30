"""Módulo 3 · Consolidación de informes (Director y jefe; los técnicos no tienen acceso)."""
from __future__ import annotations

import datetime as dt
import math
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, current_app, g, render_template, request

from .. import audit, users
from ..reports import export, service
from ..security.authz import require_perm

bp = Blueprint("consolidation", __name__, url_prefix="/consolidacion")


def local_date_to_utc(value: str, tz_name: str, end: bool = False):
    """Convierte una fecha local (AAAA-MM-DD) al límite UTC correspondiente; None si no es válida."""
    try:
        day = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if end:
        day += dt.timedelta(days=1)
    local = dt.datetime.combine(day, dt.time.min, tzinfo=ZoneInfo(tz_name))
    return local.astimezone(dt.timezone.utc).replace(tzinfo=None)


def page_number() -> int:
    try:
        return max(1, min(int(request.args.get("page", 1)), 100000))
    except ValueError:
        return 1


def _filters() -> dict:
    """Filtros de la consulta (lista y exportación), validados contra listas blancas."""
    settings = current_app.config["AJS_SETTINGS"]
    args = request.args
    filters = {
        "code": args.get("codigo", "").strip()[:40],
        "serial": args.get("serie", "").strip()[:100],
        "brand": args.get("marca", "").strip()[:100],
        "sort": args.get("orden") if args.get("orden") in service.SORTS else "recientes",
        # «ENVIADO» (informes antiguos) se consulta como «Cerrado».
        "status": service.CERRADO if args.get("estado") == service.ENVIADO else (
            args.get("estado") if args.get("estado") in service.FILTER_STATUSES else ""),
        "date_field": "closed" if args.get("fecha_por") in ("closed", "sent") else "created",
        "desde": args.get("desde", ""),
        "hasta": args.get("hasta", ""),
    }
    try:
        filters["owner_id"] = int(args.get("tecnico", "") or 0) or None
    except ValueError:
        filters["owner_id"] = None
    filters["from_utc"] = local_date_to_utc(filters["desde"], settings.timezone)
    filters["to_utc"] = local_date_to_utc(filters["hasta"], settings.timezone, end=True)
    return filters


@bp.route("")
@require_perm("module.consolidation")
def index():
    settings = current_app.config["AJS_SETTINGS"]
    filters = _filters()
    page = page_number()
    rows, total = service.search(g.user, filters, page, settings.page_size)
    pages = max(1, math.ceil(total / settings.page_size))
    return render_template("consolidation.html", rows=rows, total=total, page=page, pages=pages,
                           filters=filters, owners=users.technicians_and_owners(), brands=service.brands(g.user))


@bp.route("/exportar.xlsx")
@require_perm("module.consolidation")
def export_xlsx():
    """Libro Excel con un informe por fila (por consecutivo AJ-…), con los mismos filtros de la consulta."""
    settings = current_app.config["AJS_SETTINGS"]
    filters = _filters()
    rows, truncated = service.export_rows(g.user, filters)
    content = export.workbook(rows, current_app.config["AJS_REPORT_TEMPLATE"], settings.timezone, truncated)
    # La exportación se registra antes de entregar el archivo; si el registro falla, no se entrega.
    audit.write_standalone(audit.REPORTS_EXPORTED, "EXITO", object_type="exportacion", object_id="xlsx",
                           detail={"informes": len(rows), "truncado": truncated,
                                   "primero": rows[0]["code"] if rows else None,
                                   "ultimo": rows[-1]["code"] if rows else None,
                                   "filtros": {k: v for k, v in filters.items()
                                               if v and k not in ("from_utc", "to_utc")}})
    stamp = dt.datetime.now(ZoneInfo(settings.timezone)).strftime("%Y%m%d_%H%M")
    return Response(content, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="informes_AJ_System_{stamp}.xlsx"',
                             "Cache-Control": "no-store"})
