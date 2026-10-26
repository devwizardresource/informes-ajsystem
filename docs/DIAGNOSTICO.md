# Diagnóstico inicial (Fase 1) · 2026-10-19

## Estado encontrado

| Elemento | Hallazgo | Verificado |
|---|---|---|
| Carpeta del proyecto `informes-ajsystem/` | **Vacía**: sin código, configuración, base de datos ni plantilla de informes | Sí (`ls -la`, 0 archivos) |
| Repositorio git | La carpeta no es un repositorio git | Sí |
| Proyecto hermano `../ajsystem/` | Sitio corporativo: Next.js 16 (Node.js) exportado como estático a **GitHub Pages** y un esqueleto FastAPI con un endpoint `/contact` | Sí (lectura, sin modificar) |
| Backend FastAPI de `../ajsystem` | **No funcional**: `main.py` importa `core.config` y `routes.contact`, que no existen | Sí |
| Base de datos configurada | Ninguna. El `.env.example` de `../ajsystem` solo define CORS y la ruta de un archivo de registro | Sí (claves revisadas, valores no impresos) |
| Plantilla de informes reutilizable | Ninguna. Solo hay logos en `../ajsystem/resources/logos/` (no se usaron: la plantilla institucional está pendiente) | Sí |
| Alojamiento actual | GitHub Pages, mediante un workflow que construye con Node.js. **Solo sirve archivos estáticos: no ejecuta Python ni MySQL** | Sí (workflow `deploy-pages.yml`) |
| Entorno local | Ubuntu 24.04, Python 3.12.3, venv operativo, Docker disponible, Pango/HarfBuzz instalados; **sin servidor ni clientes MySQL** | Sí |

## Decisiones

1. **Estructura nueva en `informes-ajsystem/`**: no había código que corregir ni completar. `../ajsystem` es otro producto (sitio corporativo) basado en Node.js, que el encargo prohíbe; no se modificó.
2. **No se reutilizó el esqueleto FastAPI**: está incompleto y su modelo de CORS sin sesiones no encaja con una aplicación web con sesiones y formularios. Se eligió Flask (ver [DEPENDENCIAS.md](DEPENDENCIAS.md)), compatible con WSGI y, por tanto, con los alojamientos compartidos habituales (Passenger/cPanel).
3. **MySQL de desarrollo con Docker** (`mysql:8.4`, solo local y en pruebas). No forma parte del sistema desplegado.
4. **Las migraciones las ejecuta una cuenta DBA**: con el binlog activo, MySQL exige SUPER para crear disparadores (error 1419, comprobado con la cuenta propietaria).

## Problemas y riesgos detectados

- **Alojamiento incompatible** con Python y MySQL. Es una decisión del propietario: ver [DESPLIEGUE.md](DESPLIEGUE.md). No se migró nada.
- **Plantilla oficial no disponible**: los campos de auditoría y el diseño del PDF son provisionales.
- El perfil RESPONSABLE se muestra como **«Director»** desde la migración 0007; sigue siendo configurable sin tocar el código (`set-role-name`).

## Dependencias pendientes de verificar en el alojamiento definitivo

Versión de Python (3.12 o superior), MySQL 8.4 o compatible (8.0 o superior, con CHECK y JSON), Pango 1.44 o superior para WeasyPrint, HTTPS, ejecución de un proceso WSGI, `mysqldump`/`mysql` para los respaldos y permisos de archivos.
