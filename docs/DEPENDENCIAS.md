# Dependencias

Todas son bibliotecas de Python con licencias permisivas. No se usa Node.js, npm, CDN ni servicios externos. Versiones fijadas el 2026-10-19 (las últimas publicadas en PyPI).

| Paquete | Versión | Función | Justificación | Riesgos y mitigación |
|---|---|---|---|---|
| Flask | 3.1.3 | Framework web WSGI (rutas, plantillas, CLI) | Maduro y mínimo; WSGI es compatible con Passenger y los alojamientos compartidos; no impone ORM ni componentes innecesarios | Configuración insegura por defecto → la endurece `create_app` (cookies, CSP, tamaño máximo) |
| Werkzeug | 3.1.9 | Base HTTP de Flask; `ProxyFix` | Dependencia directa de Flask | `ProxyFix` solo se activa con un proxy autorizado |
| Jinja2 | 3.1.6 | Plantillas HTML con autoescape | Escape automático contra XSS | Uso de `|safe` prohibido por convención (no se usa) |
| PyMySQL | 1.2.3 | Controlador MySQL en Python puro | No necesita compiladores ni bibliotecas del sistema (facilita el alojamiento compartido); admite TLS | Rendimiento algo menor que los controladores en C, suficiente para el volumen previsto |
| argon2-cffi | 25.1.0 | Hash Argon2id | Algoritmo recomendado (OWASP/RFC 9106) | Coste de CPU y memoria (64 MiB por verificación), aceptable para pocos usuarios |
| python-dotenv | 1.2.4 | Lectura de `.env` en desarrollo | Comodidad en local; en producción se ignora | Ninguno relevante |
| WeasyPrint | 70.0 | HTML/CSS → PDF en el servidor | Python puro sobre Pango; soporta CSS de impresión (`@page`, contadores) | **Requiere Pango ≥ 1.44 en el sistema**: verificar en el alojamiento. Recursos externos bloqueados |
| pytest (solo desarrollo) | 9.1.1 | Pruebas automatizadas | Estándar de facto | — |

Dependencias transitivas relevantes: `itsdangerous`, `click`, `blinker`, `MarkupSafe` (Flask); `pydyf`, `tinycss2`, `cssselect2`, `Pyphen`, `fonttools`, `tinyhtml5` (WeasyPrint); `cffi` (argon2 y WeasyPrint).

Herramientas de desarrollo (no forman parte del sistema): Docker con la imagen oficial `mysql:8.4`, para el MySQL local y para `mysqldump`/`mysql` cuando no están instalados.

## Pendientes de autorización

- **Servidor WSGI de producción** (p. ej. `gunicorn`): según el alojamiento elegido.
- **`pip-audit`** (PyPA) para revisar vulnerabilidades conocidas de las dependencias en CI o antes de cada despliegue.

## Mantenimiento

Revise cada mes `pip list --outdated` y los avisos de seguridad de cada proyecto; actualice, ejecute `pytest` completo y despliegue.
