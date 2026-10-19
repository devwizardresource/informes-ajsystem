"""Copias de seguridad y restauración (mysqldump / mysql).

- Las credenciales se pasan en un archivo temporal 0600 (--defaults-extra-file), nunca en la línea
  de comandos ni en la salida.
- Cada copia genera: <nombre>.sql.gz (0600), <nombre>.sha256 y <nombre>.manifest.json con el
  número de filas por tabla y la cabeza de la cadena de auditoría para verificar la restauración.
- La restauración exige un destino explícito y se niega a sobrescribir la base activa salvo
  confirmación expresa.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import db
from .audit import verify_chain

TABLES = ["roles", "permissions", "role_permissions", "users", "sessions", "report_statuses", "report_counters",
          "reports", "report_versions", "report_events", "audit_log", "audit_chain_head", "system_settings",
          "schema_migrations"]
_DBNAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class BackupError(RuntimeError):
    pass


@contextmanager
def _defaults_file(settings, user: str, password: str):
    tmpdir = tempfile.mkdtemp(prefix="ajs-cnf-")
    os.chmod(tmpdir, 0o700)
    path = Path(tmpdir) / "client.cnf"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        # Valores entre comillas; el archivo se elimina al terminar.
        fh.write("[client]\n")
        fh.write(f'user="{user}"\npassword="{password}"\nhost="{settings.db_host}"\nport={settings.db_port}\n')
        fh.write("default-character-set=utf8mb4\n")
        if settings.db_ssl:
            fh.write("ssl-mode=REQUIRED\n")
    try:
        yield str(path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _counts(conn) -> dict:
    with conn.cursor() as cur:
        out = {}
        for t in TABLES:
            cur.execute(f"SELECT COUNT(*) AS n FROM `{t}`")
            out[t] = cur.fetchone()["n"]
        cur.execute("SELECT last_seq, last_hash FROM audit_chain_head WHERE id = 1")
        head = cur.fetchone()
    conn.commit()
    return {"rows": out, "audit_head": {"last_seq": head["last_seq"], "last_hash": head["last_hash"]}}


def create_backup(settings, user: str, password: str, out_dir: str | None = None, log=print) -> Path:
    if not _DBNAME.match(settings.db_name):
        raise BackupError("nombre de base de datos no válido")
    out = Path(out_dir or settings.backup_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = out / f"{settings.db_name}_{stamp}"
    dump_path = base.with_suffix(".sql.gz")

    conn = db.connect(settings, user=user, password=password)
    try:
        manifest = {"database": settings.db_name, "created_utc": stamp, **_counts(conn)}
    finally:
        conn.close()

    with _defaults_file(settings, user, password) as cnf:
        cmd = shlex.split(settings.mysqldump_bin) + [
            f"--defaults-extra-file={cnf}", "--single-transaction", "--routines", "--triggers", "--events",
            "--no-tablespaces", "--set-gtid-purged=OFF", "--hex-blob", settings.db_name]
        fd = os.open(dump_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for chunk in iter(lambda: proc.stdout.read(1 << 16), b""):
                gz.write(chunk)
            stderr = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0:
                dump_path.unlink(missing_ok=True)
                raise BackupError(f"mysqldump falló: {_redact(stderr)}")

    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    for path, content in ((base.with_suffix(".sha256"), f"{digest}  {dump_path.name}\n"),
                          (base.with_suffix(".manifest.json"), json.dumps({**manifest, "sha256": digest}, indent=2))):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
    log(f"Copia creada: {dump_path} (sha256 {digest[:16]}…)")
    return dump_path


def restore_backup(settings, dump_path: str, target_db: str, root_user: str, root_password: str,
                   allow_live: bool = False, apply_grants: bool = False, log=print) -> dict:
    """Restaura en target_db (que debe no existir o estar vacía) y verifica el resultado."""
    path = Path(dump_path).resolve()
    if not _DBNAME.match(target_db):
        raise BackupError("nombre de base de datos destino no válido")
    if target_db == settings.db_name and not allow_live:
        raise BackupError("El destino es la base activa. Use --allow-live solo con autorización y tras respaldarla.")
    manifest_path = Path(str(path).removesuffix(".sql.gz") + ".manifest.json")
    if not manifest_path.is_file():
        raise BackupError("falta el manifiesto de la copia")
    manifest = json.loads(manifest_path.read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != manifest["sha256"]:
        raise BackupError("La suma SHA-256 no coincide: la copia está dañada o fue alterada.")

    admin = db.connect(settings, user=root_user, password=root_password, database=None, autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = %s", (target_db,))
            if cur.fetchone()["n"] and not allow_live:
                raise BackupError("La base destino ya contiene tablas. Elija un destino vacío.")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{target_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    finally:
        admin.close()

    with _defaults_file(settings, root_user, root_password) as cnf:
        cmd = shlex.split(settings.mysql_bin) + [f"--defaults-extra-file={cnf}", target_db]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with gzip.open(path, "rb") as gz:
            try:
                shutil.copyfileobj(gz, proc.stdin, 1 << 16)
            finally:
                proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise BackupError(f"La restauración falló: {_redact(stderr)}")

    from dataclasses import replace  # noqa: PLC0415
    target = replace(settings, db_name=target_db)
    if apply_grants:
        from .dbadmin import apply_grants as _grants  # noqa: PLC0415
        _grants(target, root_user, root_password, log=log)
    conn = db.connect(target, user=root_user, password=root_password)
    try:
        counts = _counts(conn)
        chain = verify_chain(conn, settings.audit_hmac_key)
    finally:
        conn.close()
    mismatches = {t: (manifest["rows"][t], counts["rows"].get(t)) for t in manifest["rows"]
                  if manifest["rows"][t] != counts["rows"].get(t)}
    result = {"target": target_db, "row_mismatches": mismatches, "audit_chain_ok": chain["ok"],
              "audit_checked": chain["checked"], "audit_head_matches": counts["audit_head"] == manifest["audit_head"]}
    log(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _redact(text: str) -> str:
    return re.sub(r"(password\S*\s*[=:]\s*)\S+", r"\1***", text, flags=re.I)[:2000]
