#!/usr/bin/env python3
"""Crea una copia de seguridad de la base de AJ System con la cuenta propietaria.

Uso:  .venv/bin/python scripts/backup.py [--out-dir backups]
Credenciales: AJS_DB_ADMIN_USER / AJS_DB_ADMIN_PASSWORD (entorno o .env).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from ajsystem.backup import BackupError, create_backup  # noqa: E402
from ajsystem.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()
    s = load_settings()
    if not s.db_admin_user or not s.db_admin_password:
        print("Defina AJS_DB_ADMIN_USER y AJS_DB_ADMIN_PASSWORD.", file=sys.stderr)
        return 2
    try:
        create_backup(s, s.db_admin_user, s.db_admin_password, args.out_dir)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
