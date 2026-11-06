"""Tablero: consulta y filtros con dashboard.view; cargar la matriz exige dashboard.executive (Director)."""
from __future__ import annotations

import datetime as dt

import pymysql
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .. import analytics, audit, users
from ..reports import importer, inventory, verdict
from ..security.authz import require_perm

bp = Blueprint("dashboard", __name__, url_prefix="/tablero")
PRESETS = {"7": 7, "30": 30, "90": 90, "365": 365}


def _reconciliation() -> tuple[dict | None, bool]:
    """(cruce con la matriz, no_disponible). Un fallo aquí no debe dejar sin tablero al Director."""
    try:
        return inventory.reconcile(), False
    except pymysql.MySQLError:
        current_app.logger.exception("No se pudo cruzar la matriz de inventario con los informes")
        return None, True


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


@bp.route("")
@require_perm("dashboard.view")
def index():
    tz_name = current_app.config["AJS_SETTINGS"].timezone
    today = analytics.local_today(tz_name)
    args = request.args
    preset = args.get("rango") if args.get("rango") in PRESETS else None
    date_to = _date(args.get("hasta")) or today
    date_from = _date(args.get("desde"))
    if preset or date_from is None:
        date_to = today if preset else date_to
        date_from = date_to - dt.timedelta(days=PRESETS[preset or "30"] - 1)
    notice = None
    if date_from > date_to:
        date_from, date_to = date_to, date_from
        notice = "La fecha inicial era posterior a la final: se intercambiaron."
    if (date_to - date_from).days + 1 > analytics.MAX_RANGE_DAYS:
        date_from = date_to - dt.timedelta(days=analytics.MAX_RANGE_DAYS - 1)
        notice = f"El rango máximo es de {analytics.MAX_RANGE_DAYS} días; se ajustó la fecha inicial."
    owners = users.technicians_and_owners()
    try:
        owner_id = int(args.get("tecnico") or 0) or None
    except ValueError:
        owner_id = None
    if owner_id and owner_id not in {o["id"] for o in owners}:
        owner_id = None
    data = analytics.executive(date_from, date_to, owner_id, tz_name)
    matrix, matrix_unavailable = _reconciliation()
    return render_template("dashboard.html", d=data, owners=owners, owner_id=owner_id, preset=preset,
                           notice=notice, date_from=date_from, date_to=date_to, matrix=matrix,
                           matrix_unavailable=matrix_unavailable)


@bp.route("/convalidacion")
@require_perm("dashboard.view")
def validation():
    matrix, unavailable = _reconciliation()
    args = request.args
    status = args.get("estado") if args.get("estado") in inventory.STATUS_LABELS else ""
    device = args.get("tipo") if args.get("tipo") in inventory.DEVICE_LABELS else ""
    opinion = args.get("dictamen") if args.get("dictamen") in verdict.LABELS else ""
    text = args.get("buscar", "").strip()[:60]
    rows, categories, category = [], [], ""
    if matrix:
        categories = [c["label"] for c in matrix["categories"]]
        category = args.get("categoria") if args.get("categoria") in categories else ""
        needle = text.casefold()
        rows = [i for i in matrix["items"]
                if (not status or i["status"] == status)
                and (not device or i["device"] == device)
                and (not opinion or i["verdict"]["code"] == opinion)
                and (not category or (i["category"] or "Sin categoría") == category)
                and (not needle or needle in f"{i['serial'] or ''} {i['characteristics'] or ''}".casefold())]
    return render_template("validation.html", m=matrix, unavailable=unavailable, rows=rows, status=status,
                           category=category, categories=categories, text=text,
                           statuses=inventory.STATUS_LABELS, columns=inventory.COLUMNS, device=device,
                           devices=inventory.DEVICE_LABELS, opinion=opinion, opinions=verdict.LABELS)


@bp.route("/portatiles")
@require_perm("dashboard.view")
def laptops():
    matrix, unavailable = _reconciliation()
    audit_data = inventory.laptop_audit(matrix) if matrix else None
    args = request.args
    opinion = args.get("dictamen") if args.get("dictamen") in verdict.LABELS else ""
    text = args.get("buscar", "").strip()[:60]
    rows, category, owner_id = [], "", None
    if audit_data:
        category = args.get("categoria") if args.get("categoria") in {c["label"] for c in audit_data["categories"]} else ""
        try:
            owner_id = int(args.get("tecnico") or 0) or None
        except ValueError:
            owner_id = None
        if owner_id not in {t["id"] for t in audit_data["technicians"]}:
            owner_id = None
        needle = text.casefold()
        rows = [i for i in audit_data["items"]
                if (not opinion or i["verdict"]["code"] == opinion)
                and (not category or (i["category"] or "Sin categoría") == category)
                and (not owner_id or (i["report"] and i["report"]["owner_id"] == owner_id))
                and (not needle or needle in f"{i['serial'] or ''} {i['characteristics'] or ''}".casefold())]
    today = analytics.local_today(current_app.config["AJS_SETTINGS"].timezone)
    return render_template("laptops.html", m=matrix, a=audit_data, unavailable=unavailable, rows=rows, opinion=opinion,
                           opinions=verdict.LABELS, category=category, owner_id=owner_id, text=text, today=today)


@bp.route("/convalidacion/matriz", methods=["POST"])
@require_perm("dashboard.executive")
def upload_matrix():
    upload = request.files.get("archivo")
    name = (upload.filename if upload else "") or "matriz"
    try:
        total = inventory.save(g.user, upload.read() if upload else b"", name)
    except importer.ImportFileError as exc:
        audit.write_standalone(audit.INVENTORY_IMPORTED, "FALLO", object_type="inventario", object_id=name[:64],
                               detail={"motivo": str(exc)})
        flash(str(exc), "error")
    else:
        flash(f"Matriz cargada: {total} equipos. Es la matriz vigente desde ahora.", "success")
    return redirect(url_for("dashboard.validation"))
