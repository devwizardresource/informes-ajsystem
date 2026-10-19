"""AJ System — fábrica de la aplicación Flask."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, g, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from . import audit, db
from .config import load_settings
from .reports import schema
from .reports.service import STATUS_LABELS
from .security import csrf, sessions
from .security.authz import has_perm

__version__ = "1.0.0"

ERROR_MESSAGES = {
    400: "La solicitud no es válida. Recargue la página e inténtelo de nuevo.",
    401: "Debe iniciar sesión para continuar.",
    403: "No tiene permiso para realizar esta acción.",
    404: "El recurso solicitado no existe o no está disponible para su usuario.",
    405: "Operación no permitida.",
    413: "La solicitud es demasiado grande.",
    429: "Demasiadas solicitudes. Espere unos minutos.",
    500: "Se produjo un error interno. La operación no se completó. Si persiste, contacte al responsable del sistema.",
    503: "El servicio no está disponible temporalmente.",
}


def _load_dotenv() -> None:
    configured_file = os.environ.get("AJS_ENV_FILE", "").strip()
    env_file = Path(configured_file).expanduser() if configured_file else (
        Path(__file__).resolve().parent.parent / ".env"
    )
    # En producción solo se carga un archivo explícitamente indicado por el
    # proceso web; así los secretos pueden vivir fuera del árbol público y no
    # necesitan estar escritos en .htaccess.
    can_load = os.environ.get("AJS_ENV", "development") != "production" or bool(configured_file)
    if env_file.is_file() and can_load:
        from dotenv import load_dotenv  # noqa: PLC0415
        load_dotenv(env_file, override=False)


def create_app(overrides: dict | None = None) -> Flask:
    _load_dotenv()
    settings = load_settings(overrides)
    app = Flask(__name__)
    app.config.update(
        AJS_SETTINGS=settings,
        AJS_REPORT_TEMPLATE=os.environ.get("AJS_REPORT_TEMPLATE", "provisional_v0"),
        SECRET_KEY=settings.secret_key,
        # La cookie de Flask solo transporta mensajes flash (sin datos sensibles).
        SESSION_COOKIE_NAME="ajs_flash",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=settings.cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        TEMPLATES_AUTO_RELOAD=not settings.is_production,
    )
    schema.load_template(app.config["AJS_REPORT_TEMPLATE"])  # falla al arrancar si la plantilla es inválida

    if settings.trust_proxy_hops > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: PLC0415
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=settings.trust_proxy_hops,
                                x_proto=settings.trust_proxy_hops, x_host=settings.trust_proxy_hops)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_app(app)
    tz = ZoneInfo(settings.timezone)

    from .web import admin, auth, consolidation, home, logs, reports  # noqa: PLC0415
    for bp in (auth.bp, home.bp, reports.bp, consolidation.bp, logs.bp, admin.bp):
        app.register_blueprint(bp)

    @app.before_request
    def _before():
        if request.endpoint == "static":
            return None
        sessions.load_current_session()
        if not csrf.is_valid():
            audit.write_standalone(audit.CSRF_FAILED, "DENEGADO",
                                   detail={"ruta": request.path[:200], "metodo": request.method})
            return render_template("error.html", code=400, message=ERROR_MESSAGES[400]), 400
        user = g.get("user")
        if user and user["must_change_password"] and request.endpoint not in ("auth.change_password", "auth.logout"):
            return redirect(url_for("auth.change_password"))
        return None

    @app.after_request
    def _headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return csrf.attach_pre_cookie(response)

    @app.context_processor
    def _ctx():
        return {
            "csrf_token": csrf.token_for_template,
            "new_op_id": lambda: uuid.uuid4().hex,
            "current_user": g.get("user"),
            "has_perm": has_perm,
            "STATUS_LABELS": STATUS_LABELS,
            "app_version": __version__,
        }

    @app.template_filter("localdt")
    def _localdt(value, fmt="%Y-%m-%d %H:%M"):
        if not value:
            return ""
        return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime(fmt)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        code = exc.code or 500
        return render_template("error.html", code=code, message=ERROR_MESSAGES.get(code, ERROR_MESSAGES[500])), code

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        # El detalle técnico va solo al registro del servidor, nunca al usuario.
        app.logger.exception("Error no controlado en %s %s", request.method, request.path)
        try:
            if not g.get("in_tx"):
                audit.write_standalone(audit.SECURITY_ERROR, "ERROR",
                                       detail={"ruta": request.path[:200], "tipo": type(exc).__name__})
        except Exception:  # noqa: BLE001
            app.logger.exception("No se pudo registrar el error en auditoría")
        return render_template("error.html", code=500, message=ERROR_MESSAGES[500]), 500

    from .cli import register_cli  # noqa: PLC0415
    register_cli(app)
    return app
