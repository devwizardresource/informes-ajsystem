#!/usr/bin/env bash
# AJ System · auditoría de la cadena de suministro (local, GitHub Actions y antes de desplegar en Hostinger).
#
#   1. Dependencias Python fijadas en requirements.lock → pip-audit (base de datos PyPI/OSV).
#   2. Entorno Python instalado (.venv o el del sistema) → pip-audit.
#   3. requirements.lock coherente con requirements.txt (nadie cambió versiones sin regenerar hashes).
#   4. Paquetes npm del despliegue (package-lock.json) y de los recursos copiados en static/vendor → npm audit.
#   5. Integridad SHA-256 de los archivos de terceros servidos al navegador.
#
# Termina con código distinto de 0 si algo falla. Requiere red para consultar las bases de vulnerabilidades.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"
fail=0
step() { printf '\n== %s\n' "$1"; }

step "1/5 pip-audit sobre requirements.lock (instalación verificada por hash)"
"$PY" -m pip_audit --progress-spinner off --require-hashes --disable-pip -r requirements.lock || fail=1

step "2/5 pip-audit sobre el entorno instalado ($PY)"
"$PY" -m pip_audit --progress-spinner off --local --skip-editable || fail=1

step "3/5 requirements.lock coincide con requirements.txt"
if "$PY" -m uv --version >/dev/null 2>&1; then
  tmp="$(mktemp)"
  "$PY" -m uv pip compile requirements.txt --universal --python-version 3.10 --generate-hashes --no-header --quiet -o "$tmp"
  if diff -q <(grep -v '^#' requirements.lock) <(grep -v '^#' "$tmp") >/dev/null; then
    echo "requirements.lock al día."
  else
    echo "requirements.lock NO coincide: regenérelo (ver cabecera del archivo)."; fail=1
  fi
  rm -f "$tmp"
else
  echo "uv no disponible: comprobación omitida (instale requirements-dev.txt)."
fi

step "4/5 npm audit (despliegue y recursos de terceros)"
npm audit --audit-level=low || fail=1
(cd scripts/vendor-audit && npm audit --audit-level=low) || fail=1

step "5/5 Integridad SHA-256 de static/vendor y fuentes"
(cd ajsystem/static && sha256sum --check --quiet vendor/SHA256SUMS) && echo "Archivos de terceros íntegros." || fail=1

if [ "$fail" -ne 0 ]; then
  printf '\nAUDITORÍA CON HALLAZGOS: revise la salida anterior antes de desplegar.\n'
  exit 1
fi
printf '\nAuditoría superada: sin vulnerabilidades conocidas ni archivos alterados.\n'
