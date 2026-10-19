"""Módulo 4 · Registro detallado (solo lectura; responsable administrativo y jefe)."""
from __future__ import annotations

import json
import math

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .. import audit, db
from ..security.authz import require_perm
from ..security.client_info import parse_os
from .consolidation import local_date_to_utc, page_number

bp = Blueprint("logs", __name__, url_prefix="/registro")


@bp.route("")
@require_perm("module.audit_log", "audit.read")
def index():
    settings = current_app.config["AJS_SETTINGS"]
    args = request.args
    where, params = ["1=1"], []
    f = {
        "tipo": args.get("tipo", "").strip()[:48],
        "usuario": args.get("usuario", "").strip().lower()[:64],
        "resultado": args.get("resultado", "") if args.get("resultado") in audit.OUTCOMES else "",
        "ip": args.get("ip", "").strip()[:45],
        "desde": args.get("desde", ""),
        "hasta": args.get("hasta", ""),
    }
    if f["tipo"]:
        where.append("event_type = %s"); params.append(f["tipo"])
    if f["usuario"]:
        where.append("username = %s"); params.append(f["usuario"])
    if f["resultado"]:
        where.append("outcome = %s"); params.append(f["resultado"])
    if f["ip"]:
        where.append("ip = %s"); params.append(f["ip"])
    start = local_date_to_utc(f["desde"], settings.timezone)
    end = local_date_to_utc(f["hasta"], settings.timezone, end=True)
    if start:
        where.append("occurred_at >= %s"); params.append(start)
    if end:
        where.append("occurred_at < %s"); params.append(end)
    where_sql = " AND ".join(where)
    page = page_number()
    size = settings.page_size * 2
    total = db.query_one(f"SELECT COUNT(*) AS n FROM audit_log WHERE {where_sql}", tuple(params))["n"]
    rows = db.query_all(
        f"""SELECT seq, occurred_at, event_type, outcome, username, role_code, session_public_id, ip, os_reported,
                   object_type, object_id, detail
              FROM audit_log WHERE {where_sql} ORDER BY seq DESC LIMIT %s OFFSET %s""",
        (*params, size, (page - 1) * size))
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if isinstance(r["detail"], (str, bytes)) else r["detail"]
    types = [r["event_type"] for r in db.query_all("SELECT DISTINCT event_type FROM audit_log ORDER BY event_type")]
    return render_template("logs.html", rows=rows, total=total, page=page, pages=max(1, math.ceil(total / size)),
                           f=f, types=types, outcomes=sorted(audit.OUTCOMES))


END_REASONS = {"LOGOUT": "Cerró sesión", "EXPIRADA": "Caducó", "REVOCADA": "Revocada"}


@bp.route("/sesiones")
@require_perm("module.audit_log", "audit.read")
def sessions():
    """Entradas y salidas: quién inició sesión, cuándo salió, desde qué IP y sistema operativo."""
    settings = current_app.config["AJS_SETTINGS"]
    args = request.args
    f = {
        "usuario": args.get("usuario", "").strip().lower()[:64],
        "ip": args.get("ip", "").strip()[:45],
        "estado": args.get("estado", "") if args.get("estado") in ("abiertas", "cerradas") else "",
        "desde": args.get("desde", ""),
        "hasta": args.get("hasta", ""),
    }
    where, params = ["1=1"], []
    if f["usuario"]:
        where.append("(u.username = %s OR u.id_number = %s)"); params += [f["usuario"], f["usuario"].upper()]
    if f["ip"]:
        where.append("s.ip = %s"); params.append(f["ip"])
    if f["estado"] == "abiertas":
        where.append("s.ended_at IS NULL")
    elif f["estado"] == "cerradas":
        where.append("s.ended_at IS NOT NULL")
    start = local_date_to_utc(f["desde"], settings.timezone)
    end = local_date_to_utc(f["hasta"], settings.timezone, end=True)
    if start:
        where.append("s.created_at >= %s"); params.append(start)
    if end:
        where.append("s.created_at < %s"); params.append(end)
    where_sql = " AND ".join(where)
    page = page_number()
    size = settings.page_size * 2
    base = "FROM sessions s JOIN users u ON u.id = s.user_id JOIN roles r ON r.id = u.role_id"
    total = db.query_one(f"SELECT COUNT(*) AS n {base} WHERE {where_sql}", tuple(params))["n"]
    rows = db.query_all(
        f"""SELECT s.public_id, s.created_at, s.last_seen_at, s.ended_at, s.end_reason, s.ip, s.user_agent,
                   u.username, u.full_name, r.display_name AS role_name
              {base} WHERE {where_sql} ORDER BY s.created_at DESC, s.id DESC LIMIT %s OFFSET %s""",
        (*params, size, (page - 1) * size))
    now = db.utcnow()
    for r in rows:
        r["os"] = parse_os(r["user_agent"])
        finish = r["ended_at"] or now
        r["minutes"] = max(0, int((finish - r["created_at"]).total_seconds() // 60))
        r["end_label"] = END_REASONS.get(r["end_reason"], "Activa")
    return render_template("sessions.html", rows=rows, total=total, page=page,
                           pages=max(1, math.ceil(total / size)), f=f)


@bp.route("/verificar", methods=["POST"])
@require_perm("module.audit_log", "audit.verify")
def verify():
    result = audit.verify_chain(db.get_conn(), current_app.config["AJS_SETTINGS"].audit_hmac_key)
    audit.write_standalone(audit.AUDIT_VERIFIED, "EXITO" if result["ok"] else "FALLO",
                           detail={"registros_verificados": result["checked"], "integro": result["ok"],
                                   "incidencias": len(result["errors"])})
    if result["ok"]:
        flash(f"Integridad verificada: {result['checked']} registros, cadena íntegra.", "success")
    else:
        current_app.logger.error("Verificación de auditoría con incidencias: %s", result["errors"][:5])
        flash(f"ALERTA: la verificación detectó {len(result['errors'])} incidencia(s) de integridad "
              f"(primera en el registro n.º {result['errors'][0]['seq']}: {result['errors'][0]['problema']}).", "error")
    return redirect(url_for("logs.index"))
