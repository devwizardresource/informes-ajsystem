"""Ciclo de vida de los informes: máquina de estados controlada en Python y persistida en MySQL.

Estados: EN_ELABORACION → CERRADO → ENVIADO;  CERRADO/ENVIADO → REABIERTO → CERRADO → ...
Un técnico edita su informe mientras está abierto («Guardar cambios»). Al cerrarlo queda
bloqueado: para volver a editarlo debe solicitar la reapertura al jefe o al responsable.
Cada operación crítica se ejecuta en UNA transacción que incluye: bloqueo de la fila del
informe (SELECT ... FOR UPDATE), comprobación de permisos y estado, cambios, historial
(report_versions / report_events) y evento de auditoría. Si cualquiera falla, se revierte todo.
El disparador trg_reports_state_guard repite la validación de transiciones en la base de datos.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo

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
    alive = f"{alias}.deleted_at IS NULL"  # los informes eliminados no aparecen en ninguna consulta
    if "report.view_all" in user["permissions"]:
        return alive, ()
    if "report.view_sent" in user["permissions"]:
        return f"{alive} AND ({alias}.owner_id = %s OR {alias}.first_sent_at IS NOT NULL)", (user["id"],)
    return f"{alive} AND {alias}.owner_id = %s", (user["id"],)


def capabilities(report: dict, user: dict, pending_request: dict | None = None) -> dict:
    own = report["owner_id"] == user["id"]
    perms = user["permissions"]
    manage = (own and "report.edit_own" in perms) or "report.edit_any" in perms
    status = report["status_code"]
    return {
        "edit": manage and status in EDITABLE,
        "close": manage and status in EDITABLE,
        "send": manage and status == CERRADO,
        "reopen": "report.reopen" in perms and status in CLOSED,
        # El autor sin permiso de reapertura la solicita al jefe o al responsable.
        "request_reopen": (own and "report.request_reopen" in perms and "report.reopen" not in perms
                           and status in CLOSED and pending_request is None),
        "resolve_request": "report.reopen" in perms and pending_request is not None and status in CLOSED,
        "download": "report.download" in perms,
        "delete": "report.delete" in perms,
    }


_BASE_SELECT = """
    SELECT r.*, o.username AS owner_username, o.full_name AS owner_name, o.id_number AS owner_id_number,
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


def _today() -> dt.date:
    return dt.datetime.now(ZoneInfo(current_app.config["AJS_SETTINGS"].timezone)).date()


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
def _create_in(cur, user: dict, template: schema.Template, initial: dict | None, origin: str) -> tuple[int, str]:
    data = schema.empty_data(template, _today())
    data.update({k: v for k, v in (initial or {}).items() if k in data and v not in (None, "")})
    cols = schema.mapped_columns(template, data)
    prefix = current_app.config["AJS_SETTINGS"].report_code_prefix
    now = db.utcnow()
    cur.execute("INSERT INTO report_counters (year, current_value) VALUES (%s, 1) "
                "ON DUPLICATE KEY UPDATE current_value = current_value + 1", (now.year,))
    cur.execute("SELECT current_value FROM report_counters WHERE year = %s", (now.year,))
    code = f"{prefix}-{now.year}-{cur.fetchone()['current_value']:06d}"
    cur.execute(
        """INSERT INTO reports (code, owner_id, status_code, template_code, title, equipment_serial, equipment_brand,
                                data, revision, created_at, updated_at, updated_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)""",
        (code, user["id"], EN_ELABORACION, template.code, cols["title"], cols["equipment_serial"],
         cols["equipment_brand"], json.dumps(data, ensure_ascii=False, sort_keys=True), now, now, user["id"]),
    )
    report_id = cur.lastrowid
    _insert_version(cur, report_id, 1, cols, data, user, now)
    _insert_event(cur, report_id, "CREAR", None, EN_ELABORACION, 1, user, now,
                  reason=None if origin == "manual" else f"Creado desde {origin}")
    audit.write(cur, audit.REPORT_CREATED, "EXITO", object_type="informe", object_id=code,
                detail={"informe_id": report_id, "plantilla": template.code, "origen": origin})
    return report_id, code


def create(user: dict) -> int:
    if "report.create" not in user["permissions"]:
        raise ReportError("No tiene permiso para crear informes.", status=403)
    template = schema.load_template(current_app.config["AJS_REPORT_TEMPLATE"])
    with db.transaction() as cur:
        report_id, _code = _create_in(cur, user, template, None, "manual")
    return report_id


def create_many(user: dict, rows: list[dict], source_name: str) -> list[str]:
    """Importación: un informe por equipo (fila). Todo o nada, en una sola transacción."""
    if not {"report.create", "report.import"} <= set(user["permissions"]):
        raise ReportError("No tiene permiso para importar informes.", status=403)
    template = schema.load_template(current_app.config["AJS_REPORT_TEMPLATE"])
    codes = []
    with db.transaction() as cur:
        for row in rows:
            _report_id, code = _create_in(cur, user, template, row, "matriz Excel/CSV")
            codes.append(code)
        audit.write(cur, audit.REPORTS_IMPORTED, "EXITO", object_type="importacion", object_id=source_name[:64],
                    detail={"informes_creados": len(codes), "primero": codes[0] if codes else None,
                            "ultimo": codes[-1] if codes else None})
    return codes


def _apply_form(cur, report: dict, user: dict, form, expected_revision, now) -> tuple[dict, bool]:
    """Valida y persiste los datos del formulario. Devuelve (informe_actualizado, hubo_cambios)."""
    try:
        expected = int(expected_revision)
    except (TypeError, ValueError):
        raise ReportError("Solicitud no válida (revisión).", status=400)
    if expected != report["revision"]:
        raise ReportError("El informe fue modificado por otra persona desde que lo abrió. "
                          "Recargue la página para ver la versión actual antes de guardar.", code="conflicto")
    template = schema.effective_template(report["template_code"])
    current_data = schema.data_for_template(report["template_code"], report["data"])
    data, errors = schema.parse_form(template, form)
    if errors:
        raise ReportError("Revise los campos marcados.", status=422, field_errors=errors, code="validacion")
    # Keep fields from a legacy payload that are not part of the official form. This
    # preserves historical provisional values while the report is edited in the new UI.
    known = {f.id for f in template.fields}
    data = {**{k: v for k, v in report["data"].items() if k not in known}, **data}
    cols = schema.mapped_columns(template, data)
    if (data == current_data and cols["title"] == report["title"]
            and cols["equipment_serial"] == report["equipment_serial"]
            and cols["equipment_brand"] == report.get("equipment_brand")):
        return report, False
    revision = report["revision"] + 1
    cur.execute(
        """UPDATE reports SET data = %s, title = %s, equipment_serial = %s, equipment_brand = %s, revision = %s,
                              updated_at = %s, updated_by = %s
            WHERE id = %s AND revision = %s""",
        (json.dumps(data, ensure_ascii=False, sort_keys=True), cols["title"], cols["equipment_serial"],
         cols["equipment_brand"], revision, now, user["id"], report["id"], report["revision"]),
    )
    if cur.rowcount != 1:
        raise ReportError("Conflicto de concurrencia al guardar. Recargue la página.", code="conflicto")
    _insert_version(cur, report["id"], revision, cols, data, user, now)
    _insert_event(cur, report["id"], "GUARDAR", report["status_code"], report["status_code"], revision, user, now)
    audit.write(cur, audit.REPORT_SAVED, "EXITO", object_type="informe", object_id=report["code"],
                detail={"informe_id": report["id"], "revision": revision, "estado": report["status_code"],
                        "campos_modificados": sorted(k for k in data if data.get(k) != current_data.get(k))})
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
        template = schema.effective_template(report["template_code"])
        missing = schema.missing_for_close(template, schema.data_for_template(report["template_code"], report["data"]))
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


def _check_reason(reason: str, field: str = "motivo") -> str:
    reason = (reason or "").strip()
    if len(reason) < 10 or len(reason) > 1000:
        raise ReportError("Indique el motivo (entre 10 y 1000 caracteres).", status=422,
                          field_errors={field: "Motivo obligatorio (10 a 1000 caracteres)."}, code="validacion")
    return reason


def _pending_request(cur, report_id: int) -> dict | None:
    cur.execute("SELECT * FROM reopen_requests WHERE report_id = %s AND status = 'PENDIENTE' FOR UPDATE", (report_id,))
    return cur.fetchone()


def _reopen_in(cur, report: dict, user: dict, reason: str, key: str, request_row: dict | None = None) -> None:
    if report["status_code"] not in CLOSED:
        raise ReportError("Solo se pueden reabrir informes cerrados o enviados.", code="estado")
    now = db.utcnow()
    cur.execute("UPDATE reports SET status_code = %s, reopen_count = reopen_count + 1, updated_at = %s, "
                "updated_by = %s WHERE id = %s AND status_code = %s",
                (REABIERTO, now, user["id"], report["id"], report["status_code"]))
    if cur.rowcount != 1:
        raise ReportError("Conflicto de concurrencia al reabrir.", code="conflicto")
    _insert_event(cur, report["id"], "REABRIR", report["status_code"], REABIERTO, report["revision"], user, now,
                  reason=reason, key=key)
    pending = request_row or _pending_request(cur, report["id"])
    if pending:  # reabrir resuelve también la solicitud pendiente del técnico
        cur.execute("UPDATE reopen_requests SET status = 'APROBADA', resolved_by = %s, resolved_at = %s, "
                    "resolution_note = %s WHERE id = %s AND status = 'PENDIENTE'",
                    (user["id"], now, reason[:1000], pending["id"]))
    audit.write(cur, audit.REPORT_REOPENED, "EXITO", object_type="informe", object_id=report["code"],
                detail={"informe_id": report["id"], "estado_anterior": report["status_code"],
                        "estado_nuevo": REABIERTO, "motivo": reason, "operacion": "reapertura autorizada",
                        "solicitud_id": pending["id"] if pending else None})


def reopen(report_id: int, user: dict, reason: str, idem_key: str | None) -> dict:
    """Reapertura autorizada (solo responsable administrativo y jefe), con motivo obligatorio."""
    key = _check_idem(idem_key)
    if "report.reopen" not in user["permissions"]:
        raise ReportError("No tiene permiso para reabrir informes.", status=403)
    reason = _check_reason(reason)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if _event_exists(cur, report_id, "REABRIR", key):
            return {"already": True}
        _reopen_in(cur, report, user, reason, key)
    return {"already": False}


def request_reopen(report_id: int, user: dict, reason: str, idem_key: str | None) -> dict:
    """El técnico autor pide permiso para volver a editar su informe cerrado."""
    key = _check_idem(idem_key)
    if "report.request_reopen" not in user["permissions"]:
        raise ReportError("No tiene permiso para solicitar reaperturas.", status=403)
    reason = _check_reason(reason)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        if _event_exists(cur, report_id, "SOLICITAR_REAPERTURA", key):
            return {"already": True}
        if report["owner_id"] != user["id"]:
            raise ReportError("Solo el autor puede solicitar la reapertura de su informe.", status=403)
        if report["status_code"] not in CLOSED:
            raise ReportError("El informe sigue abierto: puede editarlo directamente.", code="estado")
        if _pending_request(cur, report_id):
            raise ReportError("Ya hay una solicitud de reapertura pendiente para este informe.", code="duplicado")
        now = db.utcnow()
        cur.execute("INSERT INTO reopen_requests (report_id, requested_by, reason, created_at) VALUES (%s, %s, %s, %s)",
                    (report_id, user["id"], reason, now))
        request_id = cur.lastrowid
        _insert_event(cur, report_id, "SOLICITAR_REAPERTURA", report["status_code"], report["status_code"],
                      report["revision"], user, now, reason=reason, key=key)
        audit.write(cur, audit.REOPEN_REQUESTED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "solicitud_id": request_id, "motivo": reason})
    return {"already": False}


def resolve_request(report_id: int, user: dict, approve: bool, note: str, idem_key: str | None) -> dict:
    """El jefe o el responsable aprueban (reabre el informe) o rechazan la solicitud pendiente."""
    key = _check_idem(idem_key)
    if "report.reopen" not in user["permissions"]:
        raise ReportError("No tiene permiso para resolver solicitudes de reapertura.", status=403)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        action = "REABRIR" if approve else "RECHAZAR_REAPERTURA"
        if _event_exists(cur, report_id, action, key):
            return {"already": True}
        pending = _pending_request(cur, report_id)
        if pending is None:
            raise ReportError("No hay una solicitud de reapertura pendiente para este informe.", code="estado")
        if approve:
            note = (note or "").strip() or f"Solicitud aprobada: {pending['reason']}"
            _reopen_in(cur, report, user, _check_reason(note[:1000], "nota"), key, pending)
        else:
            note = _check_reason(note, "nota")
            now = db.utcnow()
            cur.execute("UPDATE reopen_requests SET status = 'RECHAZADA', resolved_by = %s, resolved_at = %s, "
                        "resolution_note = %s WHERE id = %s AND status = 'PENDIENTE'",
                        (user["id"], now, note, pending["id"]))
            _insert_event(cur, report_id, "RECHAZAR_REAPERTURA", report["status_code"], report["status_code"],
                          report["revision"], user, now, reason=note, key=key)
            audit.write(cur, audit.REOPEN_REJECTED, "EXITO", object_type="informe", object_id=report["code"],
                        detail={"informe_id": report_id, "solicitud_id": pending["id"], "nota": note})
    return {"already": False}


def delete(report_id: int, user: dict, reason: str, idem_key: str | None) -> dict:
    """Eliminación por el responsable administrativo: el informe deja de verse en toda la aplicación.

    Es lógica (deleted_at): el historial, las versiones, los adjuntos y la auditoría se conservan,
    porque borrarlos rompería la cadena de integridad; el responsable puede restaurarlo.
    """
    _check_idem(idem_key)
    if "report.delete" not in user["permissions"]:
        raise ReportError("Solo el responsable administrativo puede eliminar informes.", status=403)
    reason = _check_reason(reason)
    with db.transaction() as cur:
        report = _lock_visible(cur, report_id, user)
        now = db.utcnow()
        cur.execute("UPDATE reports SET deleted_at = %s, deleted_by = %s, deletion_reason = %s "
                    "WHERE id = %s AND deleted_at IS NULL", (now, user["id"], reason, report_id))
        if cur.rowcount != 1:
            raise ReportNotFound()
        # Una solicitud de reapertura pendiente deja de tener sentido.
        cur.execute("UPDATE reopen_requests SET status = 'RECHAZADA', resolved_by = %s, resolved_at = %s, "
                    "resolution_note = %s WHERE report_id = %s AND status = 'PENDIENTE'",
                    (user["id"], now, "Informe eliminado por el responsable administrativo.", report_id))
        audit.write(cur, audit.REPORT_DELETED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "estado": report["status_code"], "motivo": reason,
                            "titulo": report["title"], "marca": report.get("equipment_brand"),
                            "serie": report["equipment_serial"]})
    return {"code": report["code"]}


def restore(report_id: int, user: dict) -> dict:
    if "report.delete" not in user["permissions"]:
        raise ReportError("Solo el responsable administrativo puede restaurar informes.", status=403)
    with db.transaction() as cur:
        cur.execute("SELECT id, code FROM reports WHERE id = %s AND deleted_at IS NOT NULL FOR UPDATE", (report_id,))
        row = cur.fetchone()
        if row is None:
            raise ReportNotFound()
        cur.execute("UPDATE reports SET deleted_at = NULL, deleted_by = NULL, deletion_reason = NULL WHERE id = %s",
                    (report_id,))
        audit.write(cur, audit.REPORT_RESTORED, "EXITO", object_type="informe", object_id=row["code"],
                    detail={"informe_id": report_id})
    return {"code": row["code"]}


def deleted(limit: int = 200) -> list[dict]:
    return db.query_all(
        """SELECT r.id, r.code, r.title, r.equipment_brand, r.equipment_serial, r.status_code, r.deleted_at,
                  r.deletion_reason, o.full_name AS owner_name, d.full_name AS deleted_by_name
             FROM reports r JOIN users o ON o.id = r.owner_id JOIN users d ON d.id = r.deleted_by
            WHERE r.deleted_at IS NOT NULL ORDER BY r.deleted_at DESC LIMIT %s""", (limit,))


def pending_request_for(report_id: int) -> dict | None:
    return db.query_one(
        """SELECT q.id, q.reason, q.created_at, u.full_name AS requested_by_name
             FROM reopen_requests q JOIN users u ON u.id = q.requested_by
            WHERE q.report_id = %s AND q.status = 'PENDIENTE'""", (report_id,))


def pending_requests(user: dict, limit: int = 20) -> list[dict]:
    """Solicitudes pendientes visibles para quien puede resolverlas (más antiguas primero)."""
    if "report.reopen" not in user["permissions"]:
        return []
    clause, params = visibility_clause(user)
    return db.query_all(
        f"""SELECT q.id, q.reason, q.created_at, r.id AS report_id, r.code, r.title, r.equipment_brand,
                   r.equipment_serial, u.full_name AS requested_by_name
              FROM reopen_requests q JOIN reports r ON r.id = q.report_id JOIN users u ON u.id = q.requested_by
             WHERE q.status = 'PENDIENTE' AND {clause}
             ORDER BY q.created_at LIMIT %s""", (*params, limit))


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
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.equipment_brand, r.status_code, r.revision,
                   r.created_at, r.updated_at, r.closed_at, r.last_sent_at, r.reopen_count
              FROM reports r WHERE r.owner_id = %s AND r.deleted_at IS NULL AND r.status_code IN ({placeholders})
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
    if filters.get("brand"):
        where.append("r.equipment_brand LIKE %s")
        args.append(f"%{_like_escape(filters['brand'])}%")
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
    order = SORTS.get(filters.get("sort"), SORTS["recientes"])
    total = db.query_one(f"SELECT COUNT(*) AS n FROM reports r WHERE {where_sql}", tuple(args))["n"]
    rows = db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.equipment_brand, r.status_code, r.created_at,
                   r.updated_at, r.closed_at, r.last_sent_at, r.reopen_count, o.full_name AS owner_name,
                   o.username AS owner_username
              FROM reports r JOIN users o ON o.id = r.owner_id
             WHERE {where_sql}
             ORDER BY {order} LIMIT %s OFFSET %s""",
        (*args, page_size, (page - 1) * page_size))
    return rows, total


# Ordenaciones permitidas (lista blanca). «marca» agrupa los equipos por marca y número de serie.
SORTS = {
    "recientes": "r.updated_at DESC, r.id DESC",
    "cronologico": "r.created_at ASC, r.id ASC",
    "marca": "r.equipment_brand IS NULL, r.equipment_brand ASC, r.equipment_serial ASC, r.created_at ASC",
}


def brands(user: dict) -> list[str]:
    clause, params = visibility_clause(user)
    return [r["b"] for r in db.query_all(
        f"""SELECT DISTINCT r.equipment_brand AS b FROM reports r
             WHERE {clause} AND r.equipment_brand IS NOT NULL ORDER BY b LIMIT 200""", params)]


def summary(user: dict) -> dict:
    clause, params = visibility_clause(user)
    rows = db.query_all(f"SELECT r.status_code, COUNT(*) AS n FROM reports r WHERE {clause} GROUP BY r.status_code", params)
    counts = {s: 0 for s in STATUS_LABELS}
    counts.update({r["status_code"]: r["n"] for r in rows})
    return counts


def recent_sent(user: dict, limit: int = 15) -> list[dict]:
    clause, params = visibility_clause(user)
    return db.query_all(
        f"""SELECT r.id, r.code, r.title, r.equipment_serial, r.equipment_brand, r.status_code, r.last_sent_at,
                   r.reopen_count, o.full_name AS owner_name
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
