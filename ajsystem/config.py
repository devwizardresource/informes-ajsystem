"""Configuración leída exclusivamente de variables de entorno (nunca del código fuente)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(RuntimeError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    env: str
    secret_key: str
    audit_hmac_key: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_ssl: bool
    db_admin_user: str
    db_admin_password: str
    cookie_secure: bool
    cookie_name: str
    trust_proxy_hops: int
    session_idle_minutes: int
    session_absolute_hours: int
    timezone: str
    report_code_prefix: str
    login_max_failures_user: int
    login_max_failures_ip: int
    login_window_minutes: int
    page_size: int
    mysqldump_bin: str
    mysql_bin: str
    backup_dir: str
    extra: dict = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.env == "production"


def load_settings(overrides: dict | None = None) -> Settings:
    env = os.environ.get("AJS_ENV", "development").strip().lower()
    cookie_secure = _bool("AJS_COOKIE_SECURE", env == "production")
    values = dict(
        env=env,
        secret_key=os.environ.get("AJS_SECRET_KEY", ""),
        audit_hmac_key=os.environ.get("AJS_AUDIT_HMAC_KEY", ""),
        db_host=os.environ.get("AJS_DB_HOST", "127.0.0.1"),
        db_port=_int("AJS_DB_PORT", 3306),
        db_name=os.environ.get("AJS_DB_NAME", "ajsystem"),
        db_user=os.environ.get("AJS_DB_USER", ""),
        db_password=os.environ.get("AJS_DB_PASSWORD", ""),
        db_ssl=_bool("AJS_DB_SSL", True),
        db_admin_user=os.environ.get("AJS_DB_ADMIN_USER", ""),
        db_admin_password=os.environ.get("AJS_DB_ADMIN_PASSWORD", ""),
        cookie_secure=cookie_secure,
        # El prefijo __Host- obliga a Secure, Path=/ y sin Domain (solo válido con HTTPS).
        cookie_name="__Host-ajs_session" if cookie_secure else "ajs_session",
        trust_proxy_hops=_int("AJS_TRUST_PROXY_HOPS", 0),
        session_idle_minutes=_int("AJS_SESSION_IDLE_MINUTES", 30),
        session_absolute_hours=_int("AJS_SESSION_ABSOLUTE_HOURS", 8),
        timezone=os.environ.get("AJS_TIMEZONE", "UTC"),
        report_code_prefix=os.environ.get("AJS_REPORT_CODE_PREFIX", "AJ"),
        login_max_failures_user=_int("AJS_LOGIN_MAX_FAILURES_USER", 5),
        login_max_failures_ip=_int("AJS_LOGIN_MAX_FAILURES_IP", 20),
        login_window_minutes=_int("AJS_LOGIN_WINDOW_MINUTES", 15),
        page_size=_int("AJS_PAGE_SIZE", 20),
        mysqldump_bin=os.environ.get("AJS_MYSQLDUMP_BIN", "mysqldump"),
        mysql_bin=os.environ.get("AJS_MYSQL_BIN", "mysql"),
        backup_dir=os.environ.get("AJS_BACKUP_DIR", "backups"),
    )
    if overrides:
        values.update(overrides)
    settings = Settings(**values)
    validate(settings)
    return settings


def validate(s: Settings) -> None:
    problems = []
    if len(s.secret_key) < 32:
        problems.append("AJS_SECRET_KEY ausente o demasiado corta (mínimo 32 caracteres)")
    if len(s.audit_hmac_key) < 32:
        problems.append("AJS_AUDIT_HMAC_KEY ausente o demasiado corta (mínimo 32 caracteres)")
    if s.secret_key and s.secret_key == s.audit_hmac_key:
        problems.append("AJS_SECRET_KEY y AJS_AUDIT_HMAC_KEY deben ser distintas")
    if not s.db_user:
        problems.append("AJS_DB_USER no definido")
    if s.is_production:
        if not s.cookie_secure:
            problems.append("En producción AJS_COOKIE_SECURE debe estar activo (HTTPS obligatorio)")
        if not s.db_ssl and s.db_host not in ("127.0.0.1", "localhost"):
            problems.append("En producción la conexión remota a MySQL debe usar TLS (AJS_DB_SSL=1)")
    if s.trust_proxy_hops < 0:
        problems.append("AJS_TRUST_PROXY_HOPS no puede ser negativo")
    if problems:
        # Se informa qué variable falla, nunca su valor.
        raise ConfigError("Configuración inválida: " + "; ".join(problems))
