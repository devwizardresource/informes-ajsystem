"""Adjuntos de un informe: fotos del equipo (anexo fotográfico) y firmas.

Seguridad y persistencia:
- Solo se aceptan imágenes JPEG, PNG o WebP verificadas con Pillow (no se confía en la extensión
  ni en el tipo declarado). La imagen se vuelve a codificar: se eliminan EXIF/GPS y cualquier
  contenido ajeno a los píxeles (archivos políglotas).
- Se guardan en MySQL (LONGBLOB): sobreviven a los despliegues y entran en los respaldos.
- Nunca se borran; retirar un adjunto es una marca lógica y solo con el informe abierto (lo
  impiden también los disparadores de la migración 0003).
"""
from __future__ import annotations

import hashlib
import io
import uuid
import warnings

from .. import audit, db
from . import service

KINDS = {"FOTO", "FIRMA_ELABORA", "FIRMA_RECIBE"}
SIGNATURES = {"FIRMA_ELABORA", "FIRMA_RECIBE"}
RECEIVE_SIGNERS = {"JEFE", "RESPONSABLE"}  # firma de quien recibe con el informe abierto
MAX_UPLOAD_BYTES = 15 * 1024 * 1024     # lo que se acepta del navegador
MAX_STORED_BYTES = 8 * 1024 * 1024      # lo que se guarda tras recodificar
MAX_PIXELS = 50_000_000
MAX_SIDE = {"FOTO": 2400, "FIRMA_ELABORA": 1200, "FIRMA_RECIBE": 1200}
THUMB_SIDE = 360
MAX_PHOTOS = 40
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}


class AttachmentError(service.ReportError):
    pass


def _reencode(raw: bytes, kind: str) -> tuple[bytes, str, int, int, bytes]:
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - Pillow es dependencia obligatoria
        raise AttachmentError("El procesamiento de imágenes no está disponible en el servidor.", status=503) from exc
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw))
            if img.format not in ALLOWED_FORMATS:
                raise AttachmentError("Formato no admitido. Use una foto JPEG, PNG o WebP.", status=415)
            img.load()
    except AttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de decodificación es un archivo inválido
        raise AttachmentError("El archivo no es una imagen válida.", status=415) from exc

    img = ImageOps.exif_transpose(img)  # respeta la orientación de la cámara y descarta el EXIF
    img.thumbnail((MAX_SIDE[kind], MAX_SIDE[kind]))
    out = io.BytesIO()
    if kind in SIGNATURES:
        img = img.convert("RGBA")
        img.save(out, format="PNG", optimize=True)
        mime = "image/png"
    else:
        img = img.convert("RGB")
        img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
        mime = "image/jpeg"
    content = out.getvalue()
    if len(content) > MAX_STORED_BYTES:
        raise AttachmentError("La imagen es demasiado grande incluso tras comprimirla.", status=413)
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE))
    tout = io.BytesIO()
    if kind in SIGNATURES:
        thumb.save(tout, format="PNG", optimize=True)
    else:
        thumb.save(tout, format="JPEG", quality=78, optimize=True)
    return content, mime, img.width, img.height, tout.getvalue()


def add(report_id: int, user: dict, kind: str, raw: bytes, original_name: str = "") -> dict:
    if kind not in KINDS:
        raise AttachmentError("Tipo de adjunto no válido.", status=400)
    if kind == "FIRMA_ELABORA":
        raise AttachmentError("La firma de quien elabora se administra desde Mi perfil.", status=403)
    if kind == "FIRMA_RECIBE" and not _may_sign_receive(user):
        raise AttachmentError("Solo el jefe o el Director pueden registrar la firma de quien recibe.", status=403)
    if not raw:
        raise AttachmentError("No se recibió ninguna imagen.", status=400)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise AttachmentError("La imagen supera el tamaño máximo (15 MB).", status=413)
    content, mime, width, height, thumb = _reencode(raw, kind)
    return _insert(report_id, user, kind, content, mime, width, height, thumb, original_name)


def _may_sign_receive(user: dict) -> bool:
    return user.get("role_code") in RECEIVE_SIGNERS or "report.sign_closed" in user.get("permissions", ())


def _check_writable(report: dict, user: dict, kind: str) -> None:
    caps = service.capabilities(report, user)
    if caps["edit"] or (kind == "FIRMA_RECIBE" and caps["sign_closed"]):
        return
    raise AttachmentError("El informe está cerrado o no tiene permiso para modificarlo.", status=403,
                          code="bloqueado")


def sign_with_profile(report_id: int, user: dict) -> dict:
    """El Director firma un informe cerrado o enviado con la firma guardada en su perfil."""
    if "report.sign_closed" not in user.get("permissions", ()):
        raise AttachmentError("Solo el Director puede firmar los informes cerrados.", status=403)
    signature = db.query_one("SELECT mime, width, height, content FROM user_signatures WHERE user_id = %s",
                             (user["id"],))
    if not signature:
        raise AttachmentError("Cargue primero su firma en Mi perfil.", status=409, code="sin_firma")
    content = bytes(signature["content"])
    return _insert(report_id, user, "FIRMA_RECIBE", content, signature["mime"], signature["width"],
                   signature["height"], None, "firma de perfil", require_closed=True)


def _insert(report_id: int, user: dict, kind: str, content: bytes, mime: str, width: int, height: int,
            thumb: bytes | None, original_name: str, require_closed: bool = False) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    public_id = uuid.uuid4().hex
    with db.transaction() as cur:
        report = service._lock_visible(cur, report_id, user)
        if require_closed and report["status_code"] not in service.CLOSED:
            raise AttachmentError("El informe debe estar cerrado para firmarlo como Director.", status=409,
                                  code="abierto")
        _check_writable(report, user, kind)
        now = db.utcnow()
        replaced = None
        if kind in SIGNATURES:  # una sola firma vigente por tipo
            cur.execute("SELECT public_id FROM report_attachments WHERE report_id = %s AND kind = %s "
                        "AND deleted_at IS NULL FOR UPDATE", (report_id, kind))
            prev = cur.fetchone()
            if prev:
                cur.execute("UPDATE report_attachments SET deleted_at = %s, deleted_by = %s WHERE public_id = %s",
                            (now, user["id"], prev["public_id"]))
                replaced = prev["public_id"]
        else:
            cur.execute("SELECT COUNT(*) AS n FROM report_attachments WHERE report_id = %s AND kind = 'FOTO' "
                        "AND deleted_at IS NULL", (report_id,))
            if cur.fetchone()["n"] >= MAX_PHOTOS:
                raise AttachmentError(f"Se alcanzó el máximo de {MAX_PHOTOS} fotos por informe.", status=409)
        cur.execute(
            """INSERT INTO report_attachments (public_id, report_id, kind, mime, size_bytes, width, height, sha256,
                                               content, thumb, original_name, created_by, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (public_id, report_id, kind, mime, len(content), width, height, digest, content, thumb,
             (original_name or "")[:160], user["id"], now))
        audit.write(cur, audit.ATTACHMENT_ADDED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "adjunto": public_id, "tipo": kind, "bytes": len(content),
                            "sha256": digest, "reemplaza": replaced})
    return {"id": public_id, "kind": kind, "width": width, "height": height, "size": len(content)}


def remove(report_id: int, user: dict, public_id: str) -> None:
    with db.transaction() as cur:
        report = service._lock_visible(cur, report_id, user)
        cur.execute("SELECT kind FROM report_attachments WHERE report_id = %s AND public_id = %s AND deleted_at IS NULL",
                    (report_id, public_id))
        attachment = cur.fetchone()
        if not attachment:
            raise service.ReportNotFound()
        if attachment["kind"] == "FIRMA_ELABORA":
            raise AttachmentError("La firma de quien elabora se administra desde Mi perfil.", status=403)
        if attachment["kind"] == "FIRMA_RECIBE" and not _may_sign_receive(user):
            raise AttachmentError("Solo el jefe o el Director pueden retirar la firma de quien recibe.", status=403)
        _check_writable(report, user, attachment["kind"])
        cur.execute("UPDATE report_attachments SET deleted_at = %s, deleted_by = %s "
                    "WHERE report_id = %s AND public_id = %s AND deleted_at IS NULL",
                    (db.utcnow(), user["id"], report_id, public_id))
        if cur.rowcount != 1:
            raise service.ReportNotFound()
        audit.write(cur, audit.ATTACHMENT_REMOVED, "EXITO", object_type="informe", object_id=report["code"],
                    detail={"informe_id": report_id, "adjunto": public_id})


def record_ocr(report_id: int, user: dict, public_id: str, text: str) -> None:
    """Guarda el texto reconocido de la foto (una sola vez) como evidencia del autocompletado."""
    with db.transaction() as cur:
        report = service._lock_visible(cur, report_id, user)
        if not service.capabilities(report, user)["edit"]:
            return
        cur.execute("UPDATE report_attachments SET ocr_text = %s WHERE report_id = %s AND public_id = %s "
                    "AND kind = 'FOTO' AND ocr_text IS NULL AND deleted_at IS NULL",
                    (text[:60000], report_id, public_id))


def list_for(report_id: int) -> list[dict]:
    """Adjuntos vigentes en orden cronológico (sin el contenido binario)."""
    return db.query_all(
        """SELECT a.public_id, a.kind, a.mime, a.size_bytes, a.width, a.height, a.sha256, a.original_name,
                  a.created_at, (a.ocr_text IS NOT NULL) AS has_ocr, u.full_name AS created_by_name
             FROM report_attachments a JOIN users u ON u.id = a.created_by
            WHERE a.report_id = %s AND a.deleted_at IS NULL
            ORDER BY a.created_at, a.id""", (report_id,))


def content(report_id: int, public_id: str, thumb: bool = False) -> dict | None:
    col = "COALESCE(thumb, content)" if thumb else "content"
    mime = "CASE WHEN thumb IS NOT NULL AND kind = 'FOTO' THEN 'image/jpeg' ELSE mime END" if thumb else "mime"
    return db.query_one(
        f"""SELECT public_id, kind, {mime} AS mime, sha256, {col} AS data, created_at
              FROM report_attachments WHERE report_id = %s AND public_id = %s""",
        (report_id, public_id))


def for_document(report_id: int) -> list[dict]:
    """Adjuntos vigentes con contenido, para incrustarlos en el PDF."""
    files = db.query_all(
        """SELECT a.public_id, a.kind, a.mime, a.content, a.width, a.height, a.created_at, a.sha256,
                  u.full_name AS created_by_name
             FROM report_attachments a JOIN users u ON u.id = a.created_by
            WHERE a.report_id = %s AND a.deleted_at IS NULL ORDER BY a.created_at, a.id""", (report_id,))
    if not any(a["kind"] == "FIRMA_ELABORA" for a in files):
        owner = db.query_one(
            """SELECT s.mime, s.content, s.width, s.height, s.updated_at AS created_at, s.sha256,
                      u.full_name AS created_by_name
                 FROM reports r JOIN user_signatures s ON s.user_id = r.owner_id
                 JOIN users u ON u.id = r.owner_id WHERE r.id = %s""", (report_id,))
        if owner:
            files.append({"public_id": None, "kind": "FIRMA_ELABORA", **owner})
    return files
