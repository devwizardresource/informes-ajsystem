"""Ciclo de vida de los informes: máquina de estados controlada en Python y persistida en MySQL.

Estados: EN_ELABORACION → CERRADO → ENVIADO;  CERRADO/ENVIADO → REABIERTO → CERRADO → ...
Cada operación crítica se ejecuta en UNA transacción que incluye: bloqueo de la fila del
informe (SELECT ... FOR UPDATE), comprobación de permisos y estado, cambios, historial
(report_versions / report_events) y evento de auditoría. Si cualquiera falla, se revierte todo.
El disparador trg_reports_state_guard repite la validación de transiciones en la base de datos.
"""
from __future__ import annotations

import hashlib
import json
import re

from flask import current_app

from .. import audit, db
from ..security.authz import has_perm
from . import schema

EN_ELABORACION, CERRADO, ENVIADO, REABIERTO = "EN_ELABORACION", "CERRADO", "ENVIADO", "REABIERTO"
EDITABLE = {EN_ELABORACION, REABIERTO}
CLOSED = {CERRADO, ENVIADO}
STATUS_LABELS = {EN_ELABORACION: "En elaboración", CERRADO: "Cerrado", ENVIADO: "Enviado", REABIERTO: "Reabierto"}
_IDEM_RE = re.compile(r"^[0-9a-f]{32}$")


class ReportError(Exception):
    """Operación rechazada por reglas de negocio. status: código HTTP sugerido."""

    def __init__(self, message: str, status: int = 409, field_errors: dict | None = None, code: str = "rechazada"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field_errors = field_errors or {}
        self.code = code


class ReportNotFound(ReportError):
    def __init__(self):
        super().__init__("Informe no encontrado.", status=404, code="no_encontrado")


def _content_hash(title: str, serial, data: dict) -> str:
    payload = json.dumps({"title": title, "equipment_serial": serial, "data": data},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _loads(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else (value or {})


# ---------------------------------------------------------------------------
# Visibilidad y capacidades (autorización por recurso)
# ---------------------------------------------------------------------------
def visibility_clause(user: dict, alias: str = "r") -> tuple[str, tuple]:
    """Filtro SQL que limita los informes visibles. Se aplica en TODAS las consultas."""
    if "report.view_sent" in user["permissions"]:
        return f"({alias}.owner_id = %s OR {alias}.first_sent_at IS NOT NULL)", (user["id"],)
    return f"{alias}.owner_id = %s", (user["id"],)


def capabilities(report: dict, user: dict) -> dict:
    own = report["owner_id"] == user["id"]
    manage = (own and "report.edit_own" in user["permissions"]) or "report.edit_any" in user["permissions"]
    status = report["status_code"]
    return {
        "edit": manage and status in EDITABLE,
        "close": manage and status in EDITABLE,
        "send": manage and status == CERRADO,
        "reopen": "report.reopen" in user["permissions"] and status in CLOSED,
        "download": "report.download" in user["permissions"],
    }


_BASE_SELECT = """
    SELECT r.*, o.username AS owner_username, o.full_name AS owner_name,
           ub.full_name AS updated_by_name, cb.full_name AS closed_by_name, sb.full_name AS sent_by_name
      FROM reports r
      JOIN users o ON o.id = r.owner_id
      JOIN users ub ON ub.id = r.updated_by
      LEFT JOIN users cb ON cb.id = r.closed_by
      LEFT JOIN users sb ON sb.id = r.last_sent_by
"""


def get_visible(report_id: int, user: dict) -> dict | None:
    clause, params = visibility_clause(user)
    row = db.query_one(_BASE_SELECT + f" WHERE r.id = %s AND {clause}", (report_id, *params))
    if row:
        row["data"] = _loads(row["data"])
    return row


def _lock_visible(cur, report_id: int, user: dict) -> dict:
    clause, params = visibility_clause(user)
    cur.execute(f"SELECT r.* FROM reports r WHERE r.id = %s AND {clause} FOR UPDATE", (report_id, *params))
    row = cur.fetchone()
    if row is None:
        raise ReportNotFound()
    row["data"] = _loads(row["data"])
    return row


def _check_idem(key: str | None) -> str:
    if not key or not _IDEM_RE.match(key):
        raise ReportError("Solicitud no válida: falta el identificador de operación. Recargue la página.", status=400)
    return key


def _event_exists(cur, report_id: int, action: str, key: str) -> bool:
    cur.execute("SELECT id FROM report_events WHERE report_id = %s AND action = %s AND idempotency_key = %s",
                (report_id, action, key))
    return cur.fetchone() is not None


def _insert_event(cur, report_id, action, from_status, to_status, revision, user, now, reason=None, key=None):
    cur.execute(
        """INSERT INTO report_events (report_id, action, from_status, to_status, revision, user_id, reason,
                                      idempotency_key, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (report_id, action, from_status, to_status, revision, user["id"], reason, key, now),
    )


def _insert_version(cur, report_id, revision, cols, data, user, now):
    cur.execute(
        """INSERT INTO report_versions (report_id, revision, title, equipment_serial, data, content_sha256,
                                        saved_by, saved_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (report_id, revision, cols["title"], cols["equipment_serial"],
         json.dumps(data, ensure_ascii=False, sort_keys=True),
         _content_hash(cols["title"], cols["equipment_serial"], data), user["id"], now),
    )


# ---------------------------------------------------------------------------
# Operaciones
# ---------------------------------------------------------------------------
def create(user: dict) -> int:
    if "report.create" not in user["permissions"]:
        raise ReportError("No tiene permiso para crear informes.", status=403)
    template = schema.load_template(current_app.config["AJS_REPORT_TEMPLATE"])
    data = schema.empty_data(template)
    cols = schema.mapped_columns(template, data)
    prefix = current_app.config["AJS_SETTINGS"].report_code_prefix
    with db.transaction() as cur:
        now = db.utcnow()
        cur.execute("INSERT INTO report_counters (year, current_value) VALUES (%s, 1) "
                    "ON DUPLICATE KEY UPDATE current_value = current_value + 1", (now.year,))
        cur.execute("SELECT current_value FROM report_counters WHERE year = %s", (now.year,))
        code = f"{prefix}-{now.year}-{cur.fetchone()['current_value']:06d}"
        cur.execute(
            """INSERT INTO reports (code, owner_id, status_code, template_code, title, equipment_serial, data,
                                    revision, created_at, updated_at, updated_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)""",
            (code, user["id"], EN_ELABORACION, template.code, cols["title"], cols["equipment_serial"],
             json.dumps(data, ensure_ascii=False, sort_keys=True), now, now, user["id"]),
        )
        report_id = cur.lastrowid
        _insert_version(cur, report_id, 1, cols, data, user, now)
        _insert_event(cur, report_id, "CREAR", None, EN_ELABORACION, 1, user, now)
        audit.write(cur, audit.REPORT_CREATED, "EXITO", object_type="informe", object_id=code,
                    detail={"informe_id": report_id, "plantilla": template.code})
    return report_id


def _apply_form(cur, report: dict, user: dict, form, expected_revision, now) -> tuple[dict, bool]:
    """Valida y persiste los datos del formulario. Devuelve (informe_actualizado, hubo_cambios)."""
    try:
        expected = int(expected_revision)
    except (TypeError, ValueError):
        raise ReportError("Solicitud no válida (revisión).", status=400)
    if expected != report["revision"]:
        raise ReportError("El informe fue modificado por otra persona desde que lo abrió. "
                          "Recargue la página para ver la versión actual antes de guardar.", code="conflicto")
    template = schema.load_template(report["template_code"])
    data, errors = schema.parse_form(template, form)
    if errors:
        raise ReportError("Revise los campos marcados.", status=422, field_errors=errors, code="validacion")
    cols = schema.mapped_columns(template, data)
    if data == report["data"] and cols["title"] == report["title"] and cols["equipment_serial"] == report["equipment_serial"]:
        return report, False
    revision = report["revision"] + 1
    cur.execute(
        """UPDATE reports SET data = %s, title = %s, equipment_serial = %s, revision = %s,
                              updated_at = %s, updated_by = %s
            WHERE id = %s AND revision = %s""",
        (json.dumps(data, ensure_ascii=False, sort_keys=True), cols["title"], cols["equipment_serial"],
         revision, now, user["id"], report["id"], report["revision"]),
    )
    if cur.rowcount != 1:
        raise ReportError("Conflicto de concurrencia al guardar. Recargue la página.", code="conflicto")
    _insert_version(cur, report["id"], revision, cols, data, user, now)
    _insert_event(cur, report["id"], "GUARDAR", report["status_code"], report["status_code"], revision, user, now)
    audit.write(cur, audit.REPORT_SAVED, "EXITO", object_type="informe", object_id=report["code"],
                detail={"informe_id": report["id"], "revision": revision, "estado": report["status_code"],
                        "campos_modificados": sorted(k for k in data if data.get(k) != report["data"].get(k))})
    return {**report, "data": data, **cols, "revision": revision}, True


def save(report_id: int, user: dict, form, expected_revision) -> dict:
    """Guardar cambios: persiste y mantiene el informe abierto. No cierra ni envía."""
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if not capabilities(report, user)["edit"]:
            raise ReportError("El informe no admite modificaciones en su estado actual o no tiene permiso.", status=403,
                              code="bloqueado")
        report, changed = _apply_form(cur, report, user, form, expected_revision, db.utcnow())
    return {"changed": changed, "revision": report["revision"]}


def close(report_id: int, user: dict, form, expected_revision, idem_key: str | None) -> dict:
    """«Cerrar informe y registrar»: guarda los datos definitivos y bloquea la edición."""
    key = _check_idem(idem_key)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if _event_exists(cur, report_id, "CERRAR", key):
            return {"already": True}
        if not capabilities(report, user)["close"]:
            raise ReportError("El informe no puede cerrarse en su estado actual o no tiene permiso.", status=403,
                              code="bloqueado")
        now = db.utcnow()
        report, _changed = _apply_form(cur, report, user, form, expected_revision, now)
        template = schema.load_template(report["template_code"])
        missing = schema.missing_for_close(template, report["data"])
        if missing:
            raise ReportError("Faltan campos obligatorios para cerrar el informe. Los cambios no se guardaron.", status=422,
                              field_errors=missing, code="validacion")
        cur.execute("UPDATE reports SET status_code = %s, closed_at = %s, closed_by = %s, updated_at = %s, "
                    "updated_by = %s WHERE id = %s AND status_code = %s",
                    (CERRADO, now, user["id"], now, user["id"], report_id, report["status_code"]))
        if cur.rowcount != 1:
            raise ReportError("Conflicto de concurrencia al cerrar.", code="conflicto")
        _insert_event(cur, report_id, "CERRAR", report["status_code"], CERRADO, report["revision"], user, now, key=key)
        audit.write(cur, audit.REPORT_CLOSED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "estado_anterior": report["status_code"], "estado_nuevo": CERRADO,
                            "revision": report["revision"],
                            "huella_contenido": _content_hash(report["title"], report["equipment_serial"], report["data"])})
    return {"already": False}


def send(report_id: int, user: dict, idem_key: str | None) -> dict:
    """«Enviar informe»: operación distinta del cierre. Idempotente por identificador de operación."""
    key = _check_idem(idem_key)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if _event_exists(cur, report_id, "ENVIAR", key):
            return {"already": True}
        if report["status_code"] == ENVIADO:
            raise ReportError("El informe ya fue enviado. No se registró un envío duplicado.", code="duplicado")
        if report["status_code"] != CERRADO:
            raise ReportError("Solo se pueden enviar informes cerrados. Cierre el informe primero.", code="estado")
        if not capabilities(report, user)["send"]:
            raise ReportError("No tiene permiso para enviar este informe.", status=403)
        now = db.utcnow()
        cur.execute("UPDATE reports SET status_code = %s, first_sent_at = COALESCE(first_sent_at, %s), "
                    "last_sent_at = %s, last_sent_by = %s WHERE id = %s AND status_code = %s",
                    (ENVIADO, now, now, user["id"], report_id, CERRADO))
        if cur.rowcount != 1:
            raise ReportError("Conflicto de concurrencia al enviar.", code="conflicto")
        _insert_event(cur, report_id, "ENVIAR", CERRADO, ENVIADO, report["revision"], user, now, key=key)
        audit.write(cur, audit.REPORT_SENT, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "estado_anterior": CERRADO, "estado_nuevo": ENVIADO,
                            "revision": report["revision"], "reenvio": report["first_sent_at"] is not None})
    return {"already": False}


def reopen(report_id: int, user: dict, reason: str, idem_key: str | None) -> dict:
    """Reapertura autorizada (solo responsable administrativo y jefe), con motivo obligatorio."""
    key = _check_idem(idem_key)
    if "report.reopen" not in user["permissions"]:
        raise ReportError("No tiene permiso para reabrir informes.", status=403)
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 1000:
        raise ReportError("Indique el motivo de la reapertura (entre 10 y 1000 caracteres).", status=422,
                          field_errors={"motivo": "Motivo obligatorio (10 a 1000 caracteres)."}, code="validacion")
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if _event_exists(cur, report_id, "REABRIR", key):
            return {"already": True}
        if report["status_code"] not in CLOSED:
            raise ReportError("Solo se pueden reabrir informes cerrados o enviados.", code="estado")
        now = db.utcnow()
        cur.execute("UPDATE reports SET status_code = %s, reopen_count = reopen_count + 1, updated_at = %s, "
                    "updated_by = %s WHERE id = %s AND status_code = %s",
                    (REABIERTO, now, user["id"], report_id, report["status_code"]))
        if cur.rowcount != 1:
            raise ReportError("Conflicto de concurrencia al reabrir.", code="conflicto")
        _insert_event(cur, report_id, "REABRIR", report["status_code"], REABIERTO, report["revision"], user, now,
                      reason=reason, key=key)
        audit.write(cur, audit.REPORT_REOPENED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "estado_anterior": report["status_code"],
                            "estado_nuevo": REABIERTO, "motivo": reason, "operacion": "reapertura autorizada"})
    return {"already": False}


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def history(report_id: int) -> list[dict]:
    return db.query_all(
        """SELECT e.action, e.from_status, e.to_status, e.revision, e.reason, e.created_at,
                  u.full_name, u.username
             FROM report_events e JOIN users u ON u.id = e.user_id
            WHERE e.report_id = %s ORDER BY e.id""", (report_id,))


def versions(report_id: int) -> list[dict]:
    return db.query_all(
        """SELECT v.revision, v.saved_at, v.content_sha256, u.full_name
             FROM report_versions v JOIN users u ON u.id = v.saved_by
            WHERE v.report_id = %s ORDER BY v.revision DESC""", (report_id,))


def list_own(user: dict, statuses: tuple[str, ...], limit: int = 50) -> list[dict]:
    placeholders = ",".join(["%s"] * len(statuses))
    return db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.status_code, r.revision, r.updated_at,
                   r.closed_at, r.last_sent_at, r.reopen_count
              FROM reports r WHERE r.owner_id = %s AND r.status_code IN ({placeholders})
             ORDER BY r.updated_at DESC LIMIT %s""", (user["id"], *statuses, limit))


def search(user: dict, filters: dict, page: int, page_size: int) -> tuple[list[dict], int]:
    """Consulta paginada con filtros en el servidor (Consolidación)."""
    clause, params = visibility_clause(user)
    where, args = [clause], list(params)
    if filters.get("code"):
        where.append("r.code LIKE %s")
        args.append(f"%{_like_escape(filters['code'])}%")
    if filters.get("owner_id"):
        where.append("r.owner_id = %s")
        args.append(filters["owner_id"])
    if filters.get("serial"):
        where.append("r.equipment_serial LIKE %s")
        args.append(f"%{_like_escape(filters['serial'])}%")
    if filters.get("status"):
        where.append("r.status_code = %s")
        args.append(filters["status"])
    date_col = "r.last_sent_at" if filters.get("date_field") == "sent" else "r.created_at"
    if filters.get("from_utc"):
        where.append(f"{date_col} >= %s")
        args.append(filters["from_utc"])
    if filters.get("to_utc"):
        where.append(f"{date_col} < %s")
        args.append(filters["to_utc"])
    where_sql = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) AS n FROM reports r WHERE {where_sql}", tuple(args))["n"]
    rows = db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.status_code, r.created_at, r.updated_at,
                   r.closed_at, r.last_sent_at, r.reopen_count, o.full_name AS owner_name, o.username AS owner_username
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE {where_sql}
             ORDER BY r.updated_at DESC, r.id DESC LIMIT %s OFFSET %s""",
        (*args, page_size, (page - 1) * page_size))
    return rows, total


def summary(user: dict) -> dict:
    clause, params = visibility_clause(user)
    rows = db.query_all(f"SELECT r.status_code, COUNT(*) AS n FROM reports r WHERE {clause} GROUP BY r.status_code", params)
    counts = {s: 0 for s in STATUS_LABELS}
    counts.update({r["status_code"]: r["n"] for r in rows})
    return counts


def recent_sent(user: dict, limit: int = 15) -> list[dict]:
    clause, params = visibility_clause(user)
    return db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.status_code, r.last_sent_at, r.reopen_count,
                   o.full_name AS owner_name
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE {clause} AND r.status_code = %s
             ORDER BY r.last_sent_at DESC LIMIT %s""", (*params, ENVIADO, limit))


def in_correction(user: dict, limit: int = 15) -> list[dict]:
    clause, params = visibility_clause(user)
    return db.query_all(
        f"""SELECT r.id, r.code, r.title, r.status_code, r.updated_at, o.full_name AS owner_name
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE {clause} AND r.status_code IN (%s, %s) AND r.first_sent_at IS NOT NULL
             ORDER BY r.updated_at DESC LIMIT %s""", (*params, REABIERTO, CERRADO, limit))


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:100]
