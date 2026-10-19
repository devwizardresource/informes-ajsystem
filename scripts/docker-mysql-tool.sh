#!/usr/bin/env bash
# SOLO DESARROLLO: ejecuta mysqldump/mysql desde la imagen oficial mysql:8.4 cuando el host no
# tiene instalados los clientes de MySQL. En producción, AJS_MYSQLDUMP_BIN/AJS_MYSQL_BIN deben
# apuntar a los binarios del servidor (mysqldump / mysql).
# Uso: docker-mysql-tool.sh <mysqldump|mysql> --defaults-extra-file=/ruta/archivo.cnf [args...]
set -euo pipefail
tool="$1"; shift
case "$tool" in mysqldump|mysql) ;; *) echo "herramienta no permitida: $tool" >&2; exit 2 ;; esac
mounts=()
for arg in "$@"; do
  if [[ "$arg" == --defaults-extra-file=* ]]; then
    dir="$(dirname "${arg#--defaults-extra-file=}")"
    mounts=(-v "$dir:$dir:ro")
  fi
done
exec docker run --rm -i --network host "${mounts[@]}" mysql:8.4 "$tool" "$@"
