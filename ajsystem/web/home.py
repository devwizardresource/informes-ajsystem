"""Módulo 1 · Inicio (panel personalizado por perfil)."""
from __future__ import annotations

import pymysql

from flask import Blueprint, current_app, g, render_template

from .. import db
from ..reports import service
from ..security.authz import has_perm, require_perm

bp = Blueprint("home", __name__)


@bp.route("/")
@require_perm("module.home")
def index():
    user = g.user
    ctx = {
        "drafts": [],
        "closed_pending": [],
        "sent_own": [],
        "dashboard_unavailable": False,
    }
    try:
        ctx.update(service.home_lists(user))
        if has_perm("report.view_sent") or has_perm("report.view_all"):
            ctx.update(
                summary=service.summary(user),
                recent_sent=service.recent_sent(user),
                in_correction=service.in_correction(user),
                requests=service.pending_requests(user),
            )
        if has_perm("audit.read"):
            ctx["recent_logins"] = db.query_all(
                """SELECT s.created_at, s.ended_at, s.ip, u.full_name FROM sessions s JOIN users u ON u.id = s.user_id
                    ORDER BY s.id DESC LIMIT 6""")
    except pymysql.MySQLError:
        # Keep the navigation usable if a dashboard query/table/index is unavailable.
        # The technical detail stays in the server log; the page explains the partial state.
        current_app.logger.exception("No se pudieron cargar los datos del panel de inicio para el usuario %s",
                                     user["username"])
        ctx["dashboard_unavailable"] = True
    return render_template("home.html", **ctx)
