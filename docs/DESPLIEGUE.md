# Alojamiento y despliegue

## Situación actual (verificada el 2026-10-19)

El único alojamiento identificado es **GitHub Pages**, que publica el sitio corporativo `../ajsystem/frontend` desde un workflow de GitHub Actions. GitHub Pages **solo sirve archivos estáticos**: no ejecuta Python ni ofrece MySQL. **No es compatible con AJ System.** El README de `../ajsystem` menciona Render, Railway o Fly para el backend, pero no hay nada configurado.

**No se ha migrado ni contratado nada.** La elección corresponde al propietario.

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
