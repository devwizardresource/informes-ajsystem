# AJ System · Informes de auditoría técnica de hardware

Sistema web para crear, cerrar, enviar, reabrir y consolidar informes de auditoría técnica de hardware, con control de permisos por perfil y un registro de accesos y eventos de solo inserción encadenado con HMAC.

- **Backend:** Python 3.12 + Flask 3.1 (WSGI, gunicorn)
- **Base de datos:** MySQL 8.4 LTS o MariaDB 10.6+ (Hostinger); PyMySQL, SQL parametrizado, migraciones versionadas
- **Frontend:** HTML (Jinja2), CSS y JavaScript sin CDN; fuente Ubuntu Sans y logo AJ System alojados localmente; adaptable a móviles
- **Informe:** «FORMATO DE INFORME DE HARDWARE» según la plantilla de `resources/`, con fotos anexas por equipo y **autocompletado** desde una foto (OCR en el navegador con Tesseract.js) o desde una matriz Excel/CSV
- **PDF:** WeasyPrint (HTML → PDF en el servidor), una hoja + anexo fotográfico
- **Despliegue:** GitHub → Hostinger (adaptador Node.js sin dependencias, dependencias Python verificadas por hash)

> La suite automatizada de MySQL/MariaDB cubre autenticación, permisos, informes, adjuntos y persistencia. Ejecute la verificación indicada abajo después de aplicar migraciones. Se despliega en **Hostinger** desde este repositorio (ver [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md)).

## Documentación

| Documento | Contenido |
|---|---|
| [docs/DIAGNOSTICO.md](docs/DIAGNOSTICO.md) | Diagnóstico inicial del proyecto y del entorno |
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Arquitectura, ciclo de vida, modelo entidad-relación y directorios |
| [docs/ROLES_Y_PERMISOS.md](docs/ROLES_Y_PERMISOS.md) | Perfiles, matriz de permisos y reglas de visibilidad |
| [docs/SEGURIDAD.md](docs/SEGURIDAD.md) | Medidas de seguridad implementadas |
| [docs/AUDITORIA.md](docs/AUDITORIA.md) | Registro de auditoría: eventos, integridad, alcance real y retención |
| [docs/RESPALDO_Y_RESTAURACION.md](docs/RESPALDO_Y_RESTAURACION.md) | Procedimiento de copias y restauración |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Requisitos del alojamiento, verificación y procedimiento de despliegue |
| [docs/PLANTILLA_PDF.md](docs/PLANTILLA_PDF.md) | Contrato de datos y tareas para integrar la plantilla oficial |
| [docs/DEPENDENCIAS.md](docs/DEPENDENCIAS.md) | Justificación, versiones y riesgos de cada dependencia |
| [docs/PRUEBAS.md](docs/PRUEBAS.md) | Cobertura y resultados de las pruebas |
| [docs/RIESGOS.md](docs/RIESGOS.md) | Riesgos conocidos, limitaciones y pendientes |

## Puesta en marcha local (VS Code)

Requisitos: Python 3.12+, `python3-venv`, un MySQL 8.4 accesible (o Docker para levantarlo) y las bibliotecas Pango del sistema (para el PDF; en Ubuntu: `libpango-1.0-0 libpangoft2-1.0-0`).

```bash
# 1. Entorno Python
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

# 2. Configuración
cp .env.example .env        # edite los valores; genere secretos aleatorios
chmod 600 .env

# 3. MySQL local de desarrollo (opcional, con Docker)
docker run -d --name ajsys-mysql-dev -p 127.0.0.1:3307:3306 \
  -e MYSQL_ROOT_PASSWORD="<misma que AJS_DB_ROOT_PASSWORD>" \
  -v ajsys-mysql-data:/var/lib/mysql mysql:8.4
#    (y en .env: AJS_DB_PORT=3307, AJS_DB_ROOT_PASSWORD=..., herramientas docker-mysql-tool.sh)

# 4. Base de datos: crea la base, las cuentas, aplica migraciones y privilegios mínimos
.venv/bin/flask --app wsgi db-bootstrap

# 5. Primer usuario (responsable administrativo); la contraseña se pide de forma interactiva
#    (mínimo 12 caracteres con minúscula, mayúscula, número y símbolo)
.venv/bin/flask --app wsgi create-user --username responsable --full-name "Nombre Apellido" --role RESPONSABLE --id-number 1020304050

# 6. Ejecutar
.venv/bin/flask --app wsgi run --debug        # http://127.0.0.1:5000
```

En VS Code: abra la carpeta, seleccione el intérprete `.venv` y use las configuraciones de **Ejecutar y depurar** incluidas en `.vscode/launch.json` («AJ System» y «Pruebas»). El panel **Testing** detecta pytest automáticamente.

## Comandos de operación

| Comando | Uso |
|---|---|
| `flask --app wsgi db-bootstrap` | Crea base y cuentas, migra y aplica GRANT (cuenta DBA) |
| `flask --app wsgi db-migrate` | Aplica migraciones pendientes (haga un respaldo antes) |
| `flask --app wsgi db-grants` | Actualiza los privilegios mínimos después de agregar tablas |
| `flask --app wsgi create-user --username U --full-name N --role ROL [--id-number CC]` | Alta de usuario por consola (auditada) |
| `flask --app wsgi set-role-name RESPONSABLE "Nombre visible"` | Cambia el nombre visible de un perfil |
| `flask --app wsgi verify-audit` | Verifica la cadena de auditoría (sale con 1 si hay alteraciones) |
| `python scripts/backup.py` | Copia de seguridad comprimida, con suma SHA-256 y manifiesto |
| `python scripts/restore.py <copia> --target-db <base>` | Restaura y verifica filas y cadena de auditoría |
| `pytest` | Pruebas automatizadas (requieren MySQL y `AJS_DB_ROOT_PASSWORD`) |
| `npm run audit:security` | Auditoría de dependencias (pip-audit, npm audit, lock con hashes, integridad SHA-256) |

## Estructura

```
ajsystem/                 Paquete de la aplicación
  __init__.py             Fábrica Flask, cabeceras de seguridad, CSRF, gestión de errores
  config.py               Configuración desde variables de entorno (validada)
  db.py                   Conexión MySQL y transacciones
  audit.py                Registro de auditoría centralizado (HMAC encadenado)
  users.py                Gestión de usuarios (auditada)
  documents.py            Contrato de datos del documento y motor PDF
  backup.py               Copias y restauración
  migrations.py, dbadmin.py, cli.py
  reports/schema.py       Campos del informe definidos por plantilla JSON
  reports/service.py      Máquina de estados, visibilidad y solicitudes de reapertura
  reports/attachments.py  Fotos y firmas (validadas, sin EXIF, guardadas en la base)
  reports/extract.py      Autocompletado: texto de la foto (OCR) o fila de Excel → campos
  reports/importer.py     Importación Excel/CSV (un informe por equipo)
  reports/sheet.py        Usa resources/Plantilla_Informe_AJ_System.html como hoja del informe (pantalla y PDF)
  security/               Contraseñas, sesiones, CSRF, autorización, datos del cliente
  web/                    Rutas: auth, home, reports, consolidation, logs, admin
  templates/, static/     HTML, CSS (app.css, sheet.css) y JS; logo, Ubuntu Sans y motor OCR en static/
  report_templates/       Campos: aj_v1.json (oficial) y provisional_v0.json (informes antiguos)
  document_templates/     PDF: aj_v1.html (+ assets/ logo y fuentes)
resources/                Plantilla del informe entregada por el propietario (fuente única del formato)
hostinger/, server.js     Arranque en Hostinger: integridad, Python verificado por hash, proxy a gunicorn
migrations/               SQL versionado (0001 esquema, 0002 datos base, 0003 plantilla/adjuntos/reapertura, 0004 eliminación, 0005 perfiles/invitaciones/permisos)
sql/grants_app.sql        Privilegios mínimos de la cuenta de la aplicación
scripts/                  backup.py, restore.py, security_audit.sh, vendor-audit/, docker-mysql-tool.sh (desarrollo)
.github/                  CI (auditoría + pruebas MySQL/MariaDB) y Dependabot
requirements.lock         Dependencias con hashes SHA-256 (instalación en Hostinger y CI)
tests/                    Pruebas con pytest contra MySQL real
docs/                     Documentación
```

## Reglas para mantener el sistema

1. Toda consulta SQL usa parámetros (`%s`). Nunca concatene valores del usuario.
2. Toda operación que modifique datos: dentro de `db.transaction()`, con su `audit.write(cur, ...)` en la misma transacción.
3. Toda ruta nueva: `@require_perm(...)` y, si actúa sobre un informe, la comprobación por recurso de `reports/service.py`.
4. Los cambios de esquema van en una migración nueva (`migrations/0006_...sql`); las aplicadas no se editan, porque el ejecutor verifica su suma.
5. Nada de scripts ni estilos en línea: la CSP los bloquea.
# informes-ajsystem
