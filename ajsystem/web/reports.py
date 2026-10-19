"""Módulo 2 · Creación de informe (y acciones del ciclo de vida sobre un informe concreto)."""
from __future__ import annotations

import re

from flask import (Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from .. import audit, db, documents
from ..reports import attachments, extract, importer, schema, service, sheet
from ..security.authz import deny, has_perm, login_required, require_perm

bp = Blueprint("reports", __name__, url_prefix="/informes")
UPLOAD_ENDPOINTS = {"reports.attachment_add", "reports.import_matrix"}
_PUBLIC_ID = re.compile(r"^[0-9a-f]{32}$")


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


def _reject_json(exc: service.ReportError, report_id: int, operation: str):
    if isinstance(exc, service.ReportNotFound):
        deny("informe inexistente o no visible para el usuario", object_type="informe", object_id=report_id, status=404)
    audit.write_standalone(
        audit.ACCESS_DENIED if exc.status == 403 else audit.REPORT_OP_REJECTED,
        "DENEGADO" if exc.status == 403 else "FALLO", object_type="informe", object_id=report_id,
        detail={"operacion": operation, "motivo": exc.message, "codigo": exc.code})
    return jsonify(error=exc.message), exc.status


def _visible_or_404(report_id: int) -> dict:
    report = service.get_visible(report_id, g.user)
    if report is None:
        deny("informe inexistente o no visible para el usuario", object_type="informe", object_id=report_id, status=404)
    return report


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
    flash("Informe creado. Puede tomar una foto del equipo para autocompletarlo. Recuerde guardar los cambios.",
          "success")
    return redirect(url_for("reports.detail", report_id=report_id))


# ---------------------------------------------------------------------------
# Importación desde Excel / CSV (un informe por equipo)
# ---------------------------------------------------------------------------
@bp.route("/importar/plantilla.xlsx")
@require_perm("module.reports", "report.import")
def import_template():
    template = schema.load_template(current_app.config["AJS_REPORT_TEMPLATE"])
    return Response(importer.template_workbook(template),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_importacion_AJ_System.xlsx"'})


@bp.route("/importar", methods=["POST"])
@require_perm("module.reports", "report.create", "report.import")
def import_matrix():
    upload = request.files.get("archivo")
    template = schema.load_template(current_app.config["AJS_REPORT_TEMPLATE"])
    name = (upload.filename if upload else "") or "matriz"
    try:
        rows, columns = importer.parse(upload.read() if upload else b"", name, template)
        codes = service.create_many(g.user, rows, name)
    except importer.ImportFileError as exc:
        audit.write_standalone(audit.REPORTS_IMPORTED, "FALLO", object_type="importacion", object_id=name[:64],
                               detail={"motivo": str(exc)})
        flash(str(exc), "error")
        return redirect(url_for("reports.index"))
    flash(f"Se crearon {len(codes)} informe(s) ({codes[0]} a {codes[-1]}), uno por equipo. "
          f"Columnas reconocidas: {', '.join(columns)}.", "success")
    return redirect(url_for("reports.index"))


# ---------------------------------------------------------------------------
# Detalle, guardado y ciclo de vida
# ---------------------------------------------------------------------------
def _render_detail(report: dict, *, form_values: dict | None = None, errors: dict | None = None, status: int = 200):
    template = schema.load_template(report["template_code"])
    pending = service.pending_request_for(report["id"])
    caps = service.capabilities(report, g.user, pending)
    files = attachments.list_for(report["id"])
    signatures = {a["kind"]: url_for("reports.attachment_get", report_id=report["id"], public_id=a["public_id"])
                  for a in files if a["kind"] in attachments.SIGNATURES}
    # La hoja es la plantilla del propietario (resources/), rellenada con los datos del informe.
    sheet_html = sheet.render(
        template, form_values if form_values is not None else report["data"], editable=caps["edit"],
        errors=errors, number=report["code"].rsplit("-", 1)[-1][-5:], year=report["code"].split("-")[1],
        registro={"usuario": report["owner_username"], "nombre": report["owner_name"],
                  "identificacion": report.get("owner_id_number")},
        signatures=signatures, logo_src=url_for("static", filename="img/logo-aj-system@2x.png"))
    return render_template(
        "report_detail.html",
        report=report, template=template, caps=caps, pending=pending, sheet_html=sheet_html,
        sheet_css_version=sheet.source().css_hash,
        photos=[a for a in files if a["kind"] == "FOTO"],
        history=service.history(report["id"]),
        versions=service.versions(report["id"]),
    ), status


@bp.route("/plantilla.css")
def template_css():
    """Hoja de estilos de la plantilla del propietario (se sirve aparte: la CSP no admite estilos en línea)."""
    return Response(sheet.css(), mimetype="text/css",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@bp.route("/<int:report_id>/eliminar", methods=["POST"])
@require_perm("report.delete")
def delete(report_id: int):
    try:
        result = service.delete(report_id, g.user, request.form.get("motivo", ""), request.form.get("op_id"))
    except service.ReportError as exc:
        return _reject(exc, report_id, "eliminar")
    flash(f"Informe {result['code']} eliminado. Puede restaurarlo desde «Informes eliminados».", "success")
    return redirect(url_for("reports.deleted_index"))


@bp.route("/eliminados")
@require_perm("report.delete")
def deleted_index():
    return render_template("reports_deleted.html", rows=service.deleted())


@bp.route("/<int:report_id>/restaurar", methods=["POST"])
@require_perm("report.delete")
def restore(report_id: int):
    try:
        result = service.restore(report_id, g.user)
    except service.ReportError as exc:
        if isinstance(exc, service.ReportNotFound):
            deny("informe eliminado inexistente", object_type="informe", object_id=report_id, status=404)
        flash(exc.message, "error")
        return redirect(url_for("reports.deleted_index"))
    flash(f"Informe {result['code']} restaurado.", "success")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>")
@login_required
def detail(report_id: int):
    if not (has_perm("module.reports") or has_perm("module.consolidation")):
        deny("sin acceso a módulos de informes")
    return _render_detail(_visible_or_404(report_id))


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
            flash(f"Cambios guardados (revisión {result['revision']}). El informe sigue abierto y puede seguir editándolo."
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


@bp.route("/<int:report_id>/solicitar-reapertura", methods=["POST"])
@login_required
def request_reopen(report_id: int):
    try:
        result = service.request_reopen(report_id, g.user, request.form.get("motivo", ""), request.form.get("op_id"))
    except service.ReportError as exc:
        return _reject(exc, report_id, "solicitar reapertura")
    flash("La solicitud ya se había registrado." if result["already"]
          else "Solicitud enviada. El jefe o el responsable administrativo decidirán si se habilita la edición.",
          "success")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>/resolver-solicitud", methods=["POST"])
@login_required
def resolve_request(report_id: int):
    if not has_perm("report.reopen"):
        deny("permiso requerido: report.reopen", object_type="informe", object_id=report_id)
    approve = request.form.get("decision") == "aprobar"
    try:
        result = service.resolve_request(report_id, g.user, approve, request.form.get("nota", ""),
                                         request.form.get("op_id"))
    except service.ReportError as exc:
        return _reject(exc, report_id, "resolver solicitud")
    if not result["already"]:
        flash("Solicitud aprobada: el informe está reabierto y el técnico puede editarlo." if approve
              else "Solicitud rechazada. El informe sigue cerrado.", "success")
    return redirect(url_for("reports.detail", report_id=report_id))


# ---------------------------------------------------------------------------
# Adjuntos: fotos del equipo y firmas
# ---------------------------------------------------------------------------
@bp.route("/<int:report_id>/adjuntos", methods=["POST"])
@login_required
def attachment_add(report_id: int):
    upload = request.files.get("archivo")
    kind = (request.form.get("tipo") or "FOTO").upper()
    try:
        info = attachments.add(report_id, g.user, kind, upload.read() if upload else b"",
                               upload.filename if upload else "")
    except service.ReportError as exc:
        return _reject_json(exc, report_id, "adjuntar")
    info["url"] = url_for("reports.attachment_get", report_id=report_id, public_id=info["id"])
    info["thumb"] = url_for("reports.attachment_get", report_id=report_id, public_id=info["id"], mini=1)
    return jsonify(info), 201


@bp.route("/<int:report_id>/adjuntos/<public_id>")
@login_required
def attachment_get(report_id: int, public_id: str):
    if not _PUBLIC_ID.match(public_id):
        deny("identificador de adjunto inválido", object_type="informe", object_id=report_id, status=404)
    _visible_or_404(report_id)
    row = attachments.content(report_id, public_id, thumb=request.args.get("mini") == "1")
    if row is None:
        deny("adjunto inexistente", object_type="informe", object_id=report_id, status=404)
    # El contenido de un adjunto nunca cambia (identificador único): se puede guardar en caché privada.
    return Response(bytes(row["data"]), mimetype=row["mime"], headers={
        "Cache-Control": "private, max-age=86400, immutable",
        "Content-Disposition": f'inline; filename="{public_id}.{"png" if row["mime"] == "image/png" else "jpg"}"',
        "ETag": row["sha256"],
    })


@bp.route("/<int:report_id>/adjuntos/<public_id>/retirar", methods=["POST"])
@login_required
def attachment_remove(report_id: int, public_id: str):
    try:
        attachments.remove(report_id, g.user, public_id)
    except service.ReportError as exc:
        if request.accept_mimetypes.best == "application/json":
            return _reject_json(exc, report_id, "retirar adjunto")
        return _reject(exc, report_id, "retirar adjunto")
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True)
    flash("Adjunto retirado del informe.", "success")
    return redirect(url_for("reports.detail", report_id=report_id))


@bp.route("/<int:report_id>/autocompletar", methods=["POST"])
@login_required
def autofill(report_id: int):
    """Recibe el texto reconocido en la foto (OCR en el navegador) y devuelve los campos sugeridos."""
    report = _visible_or_404(report_id)
    if not service.capabilities(report, g.user)["edit"]:
        return _reject_json(service.ReportError("El informe está cerrado: no se puede autocompletar.", status=403,
                                                code="bloqueado"), report_id, "autocompletar")
    text = (request.form.get("texto") or "")[:20000]
    template = schema.load_template(report["template_code"])
    suggestions = extract.from_text(text, template)
    public_id = request.form.get("adjunto", "")
    if _PUBLIC_ID.match(public_id) and text.strip():
        attachments.record_ocr(report_id, g.user, public_id, text)
    audit.write_standalone(audit.AUTOFILL_USED, "EXITO", object_type="informe", object_id=report["code"],
                           detail={"informe_id": report_id, "origen": "foto", "adjunto": public_id or None,
                                   "campos_sugeridos": sorted(suggestions)})
    labels = {fid: template.field(fid).label.rstrip(":") for fid in suggestions}
    return jsonify(campos=suggestions, etiquetas=labels)


@bp.route("/<int:report_id>/pdf")
@require_perm("report.download")
def download(report_id: int):
    report = _visible_or_404(report_id)
    template = schema.load_template(report["template_code"])
    now = db.utcnow()
    doc = documents.build_document_data(report, service.history(report_id), g.user, now,
                                        current_app.config["AJS_SETTINGS"].timezone,
                                        attachments.for_document(report_id))
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
