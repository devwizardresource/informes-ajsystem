"""Tablero ejecutivo (solo Director: permiso dashboard.executive). Solo lectura."""
from __future__ import annotations

import datetime as dt

from flask import Blueprint, current_app, render_template, request

from .. import analytics, users
from ..security.authz import require_perm

bp = Blueprint("dashboard", __name__, url_prefix="/tablero")
PRESETS = {"7": 7, "30": 30, "90": 90, "365": 365}


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


@bp.route("")
@require_perm("dashboard.executive")
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
    return render_template("dashboard.html", d=data, owners=owners, owner_id=owner_id, preset=preset,
                           notice=notice, date_from=date_from, date_to=date_to)
