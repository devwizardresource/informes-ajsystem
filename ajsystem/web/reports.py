"""Módulo 2 · Creación de informe (y acciones del ciclo de vida sobre un informe concreto)."""
from __future__ import annotations

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for)

from .. import audit, db, documents
from ..reports import schema, service
from ..security.authz import deny, has_perm, login_required, require_perm

bp = Blueprint("reports", __name__, url_prefix="/informes")


def _reject(exc: service.ReportError, report_id: int, operation: str):
    """Registra la operación rechazada (tras revertir la transacción) y decide la respuesta."""
    if isinstance(exc, service.ReportNotFound):
        deny("informe inexistente o no visible para el usuario", object_type="informe", object_id=report_id, status=404)
    if exc.code != "validacion":
        audit.write_standalone(
            audit.ACCESS_DENIED if exc.status == 403 else audit.REPORT_OP_REJECTED,
            "DENEGADO" if exc.status == 403 else "FALLO",
            object_type="informe", object_id=report_id,
            detail={"operacion": operation, "motivo": exc.message, "codigo": exc.code})
    flash(exc.message, "error")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("")
@require_perm("module.reports")
def index():
    user = g.user
    return render_template(
        "reports_index.html",
        drafts=service.list_own(user, (service.EN_ELABORACION, service.REABIERTO)),
        closed=service.list_own(user, (service.CERRADO,)),
        sent=service.list_own(user, (service.ENVIADO,), limit=50),
    )


@bp.route("/nuevo", methods=["POST"])
@require_perm("module.reports", "report.create")
def create():
    report_id = service.create(g.user)
    flash("Informe creado. Recuerde guardar los cambios.", "success")
    return redirect(url_for("reports.detail", report_id=report_id))


def _render_detail(report: dict, *, form_values: dict | None = None, errors: dict | None = None, status: int = 200):
    template = schema.load_template(report["template_code"])
    caps = service.capabilities(report, g.user)
    return render_template(
        "report_detail.html",
        report=report, template=template, caps=caps,
        values=form_values if form_values is not None else report["data"],
        errors=errors or {},
        history=service.history(report["id"]),
        versions=service.versions(report["id"]),
    ), status


@bp.route("/<int:report_id>")
@login_required
def detail(report_id: int):
    if not (has_perm("module.reports") or has_perm("module.consolidation")):
        deny("sin acceso a módulos de informes")
    report = service.get_visible(report_id, g.user)
    if report is None:
        deny("informe inexistente o no visible para el usuario", object_type="informe", object_id=report_id, status=404)
    return _render_detail(report)


@bp.route("/<int:report_id>/guardar", methods=["POST"])
@login_required
def save(report_id: int):
    action = request.form.get("accion", "guardar")
    try:
        if action == "cerrar":
            result = service.close(report_id, g.user, request.form, request.form.get("revision"),
                                   request.form.get("op_id"))
            flash("La operación ya se había registrado." if result["already"]
                  else "Informe cerrado y registrado. Ya puede enviarlo.", "success")
        else:
            result = service.save(report_id, g.user, request.form, request.form.get("revision"))
            flash(f"Cambios guardados (revisión {result['revision']}). El informe sigue abierto."
                  if result["changed"] else "No había cambios que guardar.", "success")
    except service.ReportError as exc:
        if exc.code == "validacion":
            report = service.get_visible(report_id, g.user)
            if report is not None:
                flash(exc.message, "error")
                values = {k: request.form.get(k) for k in report["data"]}
                return _render_detail(report, form_values=values, errors=exc.field_errors, status=422)
        return _reject(exc, report_id, "cerrar" if action == "cerrar" else "guardar")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>/enviar", methods=["POST"])
@login_required
def send(report_id: int):
    try:
        result = service.send(report_id, g.user, request.form.get("op_id"))
    except service.ReportError as exc:
        return _reject(exc, report_id, "enviar")
    flash("El envío ya se había registrado." if result["already"]
          else "Informe enviado. Ya está disponible para el responsable administrativo y el jefe.", "success")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>/reabrir", methods=["POST"])
@login_required
def reopen(report_id: int):
    if not has_perm("report.reopen"):
        deny("permiso requerido: report.reopen", object_type="informe", object_id=report_id)
    try:
        result = service.reopen(report_id, g.user, request.form.get("motivo", ""), request.form.get("op_id"))
    except service.ReportError as exc:
        return _reject(exc, report_id, "reabrir")
    flash("La reapertura ya se había registrado." if result["already"] else "Informe reabierto. La edición está habilitada.",
          "success")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>/pdf")
@require_perm("report.download")
def download(report_id: int):
    report = service.get_visible(report_id, g.user)
    if report is None:
        deny("descarga de informe inexistente o no visible", object_type="informe", object_id=report_id, status=404)
    template = schema.load_template(report["template_code"])
    now = db.utcnow()
    doc = documents.build_document_data(report, service.history(report_id), g.user, now,
                                        current_app.config["AJS_SETTINGS"].timezone)
    try:
        pdf = documents.render_pdf(doc, template.document_template)
    except documents.PdfEngineUnavailable:
        current_app.logger.exception("Motor PDF no disponible")
        audit.write_standalone(audit.REPORT_DOWNLOADED, "ERROR", object_type="informe", object_id=report["code"],
                               detail={"motivo": "motor PDF no disponible"})
        flash("No fue posible generar el PDF en este momento.", "error")
        return redirect(url_for("reports.detail", report_id=report_id))
    # La descarga se registra antes de entregar el archivo; si el registro falla, no se entrega.
    audit.write_standalone(audit.REPORT_DOWNLOADED, "EXITO", object_type="informe", object_id=report["code"],
                           detail={"informe_id": report_id, "revision": report["revision"],
                                   "estado": report["status_code"], "plantilla": template.code,
                                   "huella_contenido": doc["meta"]["huella"]})
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{report["code"]}_rev{report["revision"]}.pdf"',
        "Cache-Control": "no-store",
    })
