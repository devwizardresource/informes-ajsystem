# Alojamiento y despliegue

## Hostinger (alojamiento elegido, 2026-10-19)

El código vive en GitHub (`devwizardresource/informes-ajsystem`) y Hostinger lo importa como **aplicación Node.js desde Git**. Como AJ System es Python, el repositorio incluye un adaptador sin dependencias npm:

| Archivo | Función |
|---|---|
| `package.json` / `package-lock.json` | Lo exige el importador de Hostinger (sin dependencias npm). `npm run build` verifica y prepara Python; `npm start` arranca la app |
| `hostinger/integrity.js` | Comprueba el SHA-256 de los archivos de terceros servidos al navegador (OCR y fuentes); si alguno cambió, la app no arranca |
| `hostinger/python-env.js` | Busca Python **3.10+**, crea `.venv-runtime/` e instala `requirements.lock` con `--require-hashes --only-binary :all:` (cada paquete verificado por hash) |
| `server.js` | Escucha en `PORT`, aplica migraciones, crea el primer responsable si se configura, arranca gunicorn en un socket Unix local y le reenvía el tráfico |

### Pasos en hPanel

1. **Bases de datos → MySQL**: cree la base y su usuario (p. ej. `u123_ajsystem` / `u123_ajs`, contraseña de 16+ caracteres). Host: `localhost`. Hostinger usa MariaDB: el ejecutor de migraciones traduce la intercalación automáticamente.
2. **Sitios web → Agregar sitio → Node.js → Importar repositorio de Git**: elija este repositorio, rama `main`. Configuración de compilación:
   - Versión de Node: 20 o superior · Comando de compilación: `npm run build` · Archivo de entrada: `server.js` · Comando de inicio: `npm start`
3. **Variables de entorno** de la aplicación (nunca en el repositorio):

```
AJS_ENV=production
AJS_SECRET_KEY=<aleatorio, 48+ caracteres>
AJS_AUDIT_HMAC_KEY=<otro aleatorio distinto>
AJS_DB_HOST=localhost
AJS_DB_PORT=3306
AJS_DB_NAME=u123_ajsystem
AJS_DB_USER=u123_ajs
AJS_DB_PASSWORD=<contraseña de la base>
AJS_DB_SSL=0
AJS_COOKIE_SECURE=1
AJS_TIMEZONE=America/Bogota
# Solo para el primer despliegue (bórrelas después):
AJS_INITIAL_ADMIN_USER=responsable
AJS_INITIAL_ADMIN_NAME=Nombre Apellido
AJS_INITIAL_ADMIN_ID=<número de identificación, opcional; también sirve para entrar>
AJS_INITIAL_ADMIN_PASSWORD=<temporal; se cambia en el primer acceso>
```

   Genere los secretos con `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.
4. Despliegue. En el primer arranque se aplican las migraciones y se crea el responsable inicial (solo si no existe ninguno). Active el certificado SSL del dominio: la cookie de sesión exige HTTPS.
5. Cada `git push` a `main` se vuelve a desplegar (si activa el despliegue automático); las migraciones nuevas se aplican al arrancar. **Haga un respaldo antes de publicar una migración.**

Variables opcionales: `AJS_AUTO_MIGRATE=0` (no migrar al arrancar), `AJS_DB_ADMIN_USER`/`AJS_DB_ADMIN_PASSWORD` (cuenta distinta para migrar), `AJS_PYTHON` (ruta del intérprete), `AJS_WORKERS`/`AJS_THREADS` (por defecto 2/4), `AJS_EDGE_PROXY_HOPS` (proxies de Hostinger delante de Node, por defecto 1).

### Antes de cada despliegue

Ejecute `npm run audit:security` (o deje que lo haga la CI de GitHub en cada push) y despliegue solo con la auditoría y las pruebas en verde. La migración 0003 (plantilla oficial, fotos, solicitudes de reapertura) se aplica sola al arrancar: **haga antes un respaldo de la base desde hPanel**.

Las fotos y firmas se guardan en la base de datos (no en el disco de la app), así que sobreviven a los redespliegues de Hostinger y entran en los respaldos de la base. Con `max_allowed_packet` por defecto de MariaDB (16 MB) caben de sobra (cada foto se guarda recomprimida, normalmente < 1,5 MB).

### Limitaciones en Hostinger compartido

- **Python del servidor**: si no hay Python 3.9+ la página muestra «Servicio no disponible» y el registro de la app indica el motivo. Solución: `AJS_PYTHON` o un plan VPS.
- **Una sola cuenta de base de datos**: hPanel no permite los privilegios mínimos de `sql/grants_app.sql`; la app usa la cuenta con todos los privilegios sobre su base. Los disparadores de solo inserción siguen protegiendo auditoría e historial ante la aplicación.
- **Disparadores**: si el servidor tiene registro binario sin `log_bin_trust_function_creators`, la migración 0001 fallará al crear disparadores; habría que pedirlo al soporte de Hostinger o usar VPS.
- **Respaldos**: `mysqldump` puede no estar disponible; use además las copias de bases de datos de hPanel.
- Verificado localmente el 2026-10-19 con MariaDB 11.4: build, migraciones, disparadores, creación del responsable e inicio de sesión a través del proxy.

## Alternativas compatibles con la arquitectura autorizada (Python + MySQL)

| Opción | Ventajas | Inconvenientes / riesgos | Requisitos que comprobar |
|---|---|---|---|
| **A. VPS propio** (Ubuntu 24.04 LTS + nginx + servidor WSGI + MySQL 8.4) | Control total; WeasyPrint y `mysqldump` disponibles; permite cron para respaldos y verificaciones | Requiere administrar el sistema operativo, los parches y el firewall | Coste mensual del VPS; quién lo administra |
| **B. Hosting compartido con «Setup Python App»** (cPanel/Passenger) + MySQL del proveedor | Barato y gestionado; `wsgi.py` ya expone `application` para Passenger | Versión de Python y librerías del sistema (Pango) no controlables; quizá sin SUPER para disparadores (migrar con el soporte del proveedor); cron limitado | Python 3.12 o superior, MySQL 8.0 o superior, Pango 1.44 o superior, acceso SSH, `mysqldump` |
| **C. PaaS** (Render, Railway, Fly) + MySQL gestionado | Despliegue sencillo y HTTPS incluido | **Servicio externo de pago: requiere autorización**; MySQL gestionado aparte; disparadores con cuentas restringidas | Plan, región, copias del proveedor |

Recomendación técnica: **A** si hay quien lo administre; si no, **B**, verificando antes los requisitos de la tabla.

Para A y C hace falta además un servidor WSGI de producción (p. ej. `gunicorn`, Python, mantenido). **No está incluido en `requirements.txt` hasta que se apruebe el alojamiento.** En B, Passenger cumple esa función.

## Lista de verificación previa al despliegue

- [ ] Python 3.12 o superior disponible (`python3 --version`)
- [ ] MySQL 8.0 o superior (recomendado 8.4 LTS) y conectividad desde la aplicación, con TLS
- [ ] Cuenta DBA disponible para `db-bootstrap` / `db-migrate` (disparadores)
- [ ] El proceso web puede ejecutarse (WSGI)
- [ ] HTTPS con certificado válido; `AJS_COOKIE_SECURE=1`
- [ ] Variables de entorno definidas en el servidor (no en el repositorio); secretos nuevos y distintos de los de desarrollo
- [ ] Permisos: código de solo lectura para el usuario del proceso; `.env` 0600; `backups/` 0700
- [ ] WeasyPrint funciona: `python -c "import weasyprint"` y una descarga de prueba
- [ ] `mysqldump`/`mysql` disponibles para los respaldos; cron diario de `scripts/backup.py` y de `verify-audit`
- [ ] Copia de seguridad realizada y restauración probada **antes** de migrar
- [ ] `pytest` en verde en un entorno equivalente

## Procedimiento (opción A, orientativo)

```bash
# Como usuario de despliegue, en /srv/ajsystem
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Variables en un EnvironmentFile de systemd (0600), con AJS_ENV=production
.venv/bin/flask --app wsgi db-bootstrap --account-host localhost   # solo la primera vez (pide la clave DBA)
.venv/bin/flask --app wsgi create-user --username ... --role RESPONSABLE
# Servidor WSGI escuchando en 127.0.0.1 detrás de nginx con TLS; con un proxy, AJS_TRUST_PROXY_HOPS=1
```

Actualizaciones: respaldo → `git pull` → `pip install -r requirements.txt` → `flask db-migrate` → reinicio → `verify-audit`.
Las migraciones irreversibles o destructivas **no** se ejecutan sin respaldo verificado y autorización.

## Cuentas MySQL

| Cuenta | Uso | Privilegios |
|---|---|---|
| DBA (root) | Bootstrap, migraciones y restauración | Totales (solo en operación, nunca en la aplicación) |
| `ajsys_owner` | Copias de seguridad | `ALL` sobre la base |
| `ajsys_app` | Aplicación web | Mínimos, según `sql/grants_app.sql` |

En producción, restrinja el host de las cuentas (`--account-host localhost` o la IP del servidor de aplicación) en lugar de `%`.
