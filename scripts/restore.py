#!/usr/bin/env python3
"""Restaura una copia de AJ System en una base destino y verifica filas y cadena de auditoría.

Uso:  .venv/bin/python scripts/restore.py backups/ajsystem_XXXX.sql.gz --target-db ajsystem_restore
      [--apply-grants] [--allow-live]
Requiere una cuenta DBA (crea disparadores): --root-user (por defecto root) y su contraseña,
leída de AJS_DB_ROOT_PASSWORD o pedida de forma interactiva.
Código de salida 0 solo si la restauración coincide con el manifiesto y la cadena es íntegra.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from ajsystem.backup import BackupError, restore_backup  # noqa: E402
from ajsystem.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump")
    parser.add_argument("--target-db", required=True)
    parser.add_argument("--root-user", default="root")
    parser.add_argument("--apply-grants", action="store_true", help="aplica sql/grants_app.sql sobre el destino")
    parser.add_argument("--allow-live", action="store_true", help="permite restaurar sobre la base activa (peligroso)")
    args = parser.parse_args()
    s = load_settings()
    pw = os.environ.get("AJS_DB_ROOT_PASSWORD") or getpass.getpass(f"Contraseña de {args.root_user}: ")
    try:
        result = restore_backup(s, args.dump, args.target_db, args.root_user, pw,
                                allow_live=args.allow_live, apply_grants=args.apply_grants)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    ok = not result["row_mismatches"] and result["audit_chain_ok"] and result["audit_head_matches"]
    print("RESTAURACIÓN VERIFICADA" if ok else "RESTAURACIÓN CON DIFERENCIAS: revise el informe anterior")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
