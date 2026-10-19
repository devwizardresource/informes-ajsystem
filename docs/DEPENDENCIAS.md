# Dependencias

Versiones fijadas el 2026-10-19; todas con licencias permisivas y sin vulnerabilidades conocidas (`pip-audit`, `npm audit`). Instalación verificada por hash con `requirements.lock`. Ver la sección «Cadena de suministro» de [SEGURIDAD.md](SEGURIDAD.md).

## Python (servidor)

| Paquete | Versión | Función | Riesgos y mitigación |
|---|---|---|---|
| Flask / Werkzeug / Jinja2 | 3.1.3 / 3.1.9 / 3.1.6 | Framework web WSGI, plantillas con autoescape | Configuración endurecida en `create_app`; `ProxyFix` con un salto (proxy Node) |
| PyMySQL | 1.2.3 | Controlador MySQL/MariaDB en Python puro | Consultas siempre parametrizadas |
| argon2-cffi | 25.1.0 | Hash Argon2id | 64 MiB por verificación, aceptable para pocos usuarios |
| python-dotenv | 1.2.4 | `.env` en desarrollo / `AJS_ENV_FILE` en producción | — |
| gunicorn | 26.2.0 | Servidor WSGI de producción (lo arranca `server.js`) | Escucha solo en un socket Unix local |
| Pillow | 12.3.0 | Verificar y recodificar fotos y firmas (elimina EXIF/GPS) | Límite de píxeles; formatos en lista blanca |
| openpyxl / defusedxml | 3.1.5 / 0.7.1 | Leer la matriz Excel de importación y generar su plantilla | Solo lectura, sin fórmulas, XML endurecido, límites de tamaño |
| WeasyPrint | 70.0 | HTML/CSS → PDF | **Requiere Pango ≥ 1.44** en el servidor; recursos externos bloqueados |
| pytest, pip-audit, uv (desarrollo) | 9.1.1, 2.10.1, 0.12.22 | Pruebas, auditoría, generación del lock | No se instalan en producción |

## Navegador (alojados en `ajsystem/static`, sin CDN)

| Paquete npm | Versión | Uso | Licencia |
|---|---|---|---|
| tesseract.js + tesseract.js-core | 7.0.0 | OCR de la foto del equipo (WebAssembly, se carga solo al tomar una foto) | Apache-2.0 |
| @tesseract.js-data/spa, /eng | 1.0.0 (`4.0.0_best_int`) | Modelos de español e inglés (≈5 MB, quedan en caché del navegador) | Apache-2.0 |
| @fontsource/ubuntu-sans | 5.3.0 | Fuente Ubuntu Sans (web: WOFF2; PDF: TTF convertidos) | Ubuntu Font Licence 1.0 (libre, permite incrustar) |

Detalle y procedimiento de actualización: [static/vendor/README.md](../ajsystem/static/vendor/README.md).

## Node.js (solo Hostinger)

`package.json` no tiene dependencias: `server.js` usa solo módulos nativos de Node.

## Mantenimiento

Dependabot abre propuestas semanales. Para actualizar a mano: edite `requirements.txt`, regenere el lock (cabecera de `requirements.lock`), ejecute `bash scripts/security_audit.sh` y `pytest`, y despliegue.
