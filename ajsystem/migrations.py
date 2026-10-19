"""Ejecutor de migraciones SQL versionadas (carpeta migrations/, archivos NNNN_nombre.sql).

Se ejecuta con la cuenta propietaria (DDL). Cada migración se registra en schema_migrations
con su suma SHA-256: si un archivo ya aplicado cambia, el ejecutor se detiene.
Nota: en MySQL las sentencias DDL confirman implícitamente; una migración que falle a mitad
debe revisarse manualmente (por eso se exige respaldo antes de migrar en producción).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def split_statements(sql_text: str) -> list[str]:
    """Divide un script respetando 'DELIMITER' (igual que el cliente mysql)."""
    delimiter = ";"
    statements, buf = [], []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1]
            continue
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        if stripped.endswith(delimiter):
            stmt = "\n".join(buf).rstrip()
            stmt = stmt[: -len(delimiter)].strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path, str]]:
    found = []
    for path in sorted(directory.iterdir()):
        m = _NAME_RE.match(path.name)
        if m:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            found.append((m.group(1), path, checksum))
    return found


# MariaDB (la base de los planes compartidos de Hostinger) no reconoce la intercalación de
# MySQL 8. Se traduce al ejecutar, sin tocar los archivos (su checksum no cambia).
_MARIADB_REWRITES = (("utf8mb4_0900_ai_ci", "utf8mb4_unicode_520_ci"),)


def _is_mariadb(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION() AS v")
        return "mariadb" in str(cur.fetchone()["v"]).lower()


def default_collation(conn) -> str:
    return "utf8mb4_unicode_520_ci" if _is_mariadb(conn) else "utf8mb4_0900_ai_ci"


def _adapt(stmt: str, mariadb: bool) -> str:
    if mariadb:
        for old, new in _MARIADB_REWRITES:
            stmt = stmt.replace(old, new)
    return stmt


def apply_migrations(conn, directory: Path = MIGRATIONS_DIR, log=print) -> list[str]:
    mariadb = _is_mariadb(conn)
    with conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version     CHAR(4)     NOT NULL PRIMARY KEY,
                   filename    VARCHAR(190) NOT NULL,
                   checksum    CHAR(64)    NOT NULL,
                   applied_at  DATETIME(6) NOT NULL
               ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
        )
        cur.execute("SELECT version, checksum FROM schema_migrations")
        applied = {r["version"]: r["checksum"] for r in cur.fetchall()}
    conn.commit()

    newly = []
    for version, path, checksum in list_migrations(directory):
        if version in applied:
            if applied[version] != checksum:
                raise RuntimeError(f"La migración {path.name} ya aplicada fue modificada (checksum distinto)")
            continue
        log(f"Aplicando {path.name} ...")
        with conn.cursor() as cur:
            for stmt in split_statements(path.read_text(encoding="utf-8")):
                cur.execute(_adapt(stmt, mariadb))
            cur.execute(
                "INSERT INTO schema_migrations (version, filename, checksum, applied_at) VALUES (%s, %s, %s, UTC_TIMESTAMP(6))",
                (version, path.name, checksum),
            )
        conn.commit()
        newly.append(path.name)
    return newly
