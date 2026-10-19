"""Módulo 1 · Inicio (panel personalizado por perfil)."""
from __future__ import annotations

from flask import Blueprint, g, render_template

from .. import db
from ..reports import service
from ..security.authz import has_perm, require_perm

bp = Blueprint("home", __name__)


@bp.route("/")
@require_perm("module.home")
def index():
    user = g.user
    ctx = {
        "drafts": service.list_own(user, (service.EN_ELABORACION, service.REABIERTO)),
        "closed_pending": service.list_own(user, (service.CERRADO,)),
        "sent_own": service.list_own(user, (service.ENVIADO,), limit=20),
    }
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
    return render_template("home.html", **ctx)
