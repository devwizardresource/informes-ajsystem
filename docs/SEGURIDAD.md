# Medidas de seguridad

| # | Requisito | Implementación | Verificación |
|---|---|---|---|
| 1 | Contraseñas | Argon2id (`argon2-cffi`), rehash automático; hash ficticio para igualar tiempos. **Política:** mínimo 12 caracteres con minúscula, mayúscula, número y símbolo | `test_politica_de_contrasena` |
| 2 | Sesiones y fijación | Sesión en el servidor (MySQL); la cookie lleva un token aleatorio de 256 bits y la base solo su SHA-256; **token nuevo en cada login**, revocación del token previo | `test_fijacion_de_sesion` |
| 3 | Cookies | `HttpOnly`, `SameSite=Lax`, `Secure` y prefijo `__Host-` en producción | `test_fijacion_de_sesion` |
| 4 | CSRF | Token sincronizado por sesión en todos los POST; doble envío firmado en el login; verificación de `Origin` | `test_csrf_obligatorio`, `test_origen_externo_rechazado` |
| 5 | XSS | Autoescape de Jinja2 y CSP `script-src 'self'` sin scripts ni estilos en línea | `test_escape_xss_en_datos_del_informe` |
| 6 | Inyección SQL | Solo consultas parametrizadas; los identificadores dinámicos salen de listas blancas; `LIKE` escapado | `test_consolidacion_filtros_y_paginacion` |
| 7 | Validación en el servidor | Esquema de campos por plantilla (`reports/schema.py`), longitudes, tipos y estados | pruebas de ciclo de vida |
| 8 | Autorización | Por rol (`require_perm`) y por recurso (`visibility_clause`, `capabilities`) | `test_permissions.py` |
| 9 | Límite de intentos | 5 fallos por usuario o 20 por IP en 15 minutos (configurable), con mensaje genérico | `test_limite_de_intentos` |
| 10 | Errores | Páginas genéricas; el detalle técnico solo va al registro del servidor | `test_errores_no_exponen_detalles` |
| 11 | Secretos | Variables de entorno; `.env` excluido de git con permisos 0600; la configuración se valida al arrancar | `test_configuracion_insegura_rechazada` |
| 12 | HTTPS | Obligatorio en producción (la aplicación no arranca con `AJS_COOKIE_SECURE=0`); HSTS | `config.validate` |
| 13 | Sistema de archivos | Los PDF se generan en memoria y no se almacenan; respaldos con permisos 0600 en un directorio 0700 | `test_copia_y_restauracion` |
| 14 | Acceso a documentos | La descarga exige `report.download`, visibilidad y registro de auditoría previo | `test_tecnico_no_puede_descargar` |
| 15 | Operaciones administrativas | Auditadas en la misma transacción | `test_responsable_gestiona_usuarios_y_queda_auditado` |
| 16 | Descargas no autorizadas | 403/404 y evento `ACCESO_DENEGADO` | idem |
| 17 | Caducidad | 30 minutos de inactividad y 8 horas absolutas (configurables); cierre de sesión explícito | `test_sesion_expirada_*` |
| 18 | Respaldos | Credenciales en un archivo temporal 0600, suma SHA-256 y manifiesto; la restauración rechaza copias alteradas | `test_restauracion_rechaza_copia_alterada` |
| 19 | Cadena de suministro | Ver la sección siguiente: lock con hashes, `pip-audit`, `npm audit`, integridad SHA-256 de recursos de terceros, CI semanal y Dependabot | `scripts/security_audit.sh` |
| 20 | Pruebas antes de desplegar | `pytest` completo, incluidos los casos de seguridad | [PRUEBAS.md](PRUEBAS.md) |

Otras medidas:
- Cabeceras `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` y `Cache-Control: no-store` en las páginas autenticadas.
- Solicitudes limitadas a 1 MB, salvo la subida de fotos y la importación Excel (16 MB, solo en esas rutas).
- **Fotos y firmas:** se verifican con Pillow (JPEG/PNG/WebP reales, límite de píxeles contra bombas de descompresión) y se **vuelven a codificar**: se eliminan EXIF y GPS y cualquier contenido ajeno a la imagen. Se sirven solo a quien puede ver el informe, con `nosniff`.
- **Importación Excel/CSV:** máximo 5 MB, 500 filas y 80 columnas; tamaño descomprimido limitado (bombas zip); `openpyxl` en solo lectura sin fórmulas y con `defusedxml`.
- **OCR en el navegador:** Tesseract.js alojado en el propio servidor (sin CDN ni servicios externos: las fotos no salen del sistema). La CSP añade `'wasm-unsafe-eval'` únicamente en la página del informe y en los archivos del motor; nunca `'unsafe-eval'` ni `'unsafe-inline'`.
- Sin redirecciones abiertas tras el login (prueba `test_sin_redireccion_abierta_tras_login`).
- La IP reenviada (`X-Forwarded-For`) solo se acepta si `AJS_TRUST_PROXY_HOPS > 0`.
- La generación de PDF bloquea la carga de recursos externos (`url_fetcher` limitado a la carpeta de plantillas).
- Cuenta MySQL de la aplicación con privilegios mínimos (`sql/grants_app.sql`): sin DDL, sin `DELETE`, sin `TRIGGER`.

## Cadena de suministro (local, GitHub y Hostinger)

| Capa | Control |
|---|---|
| Python | `requirements.txt` fija versiones; `requirements.lock` fija **todas** las dependencias transitivas con su SHA-256 (generado con `uv pip compile --universal --generate-hashes`). Hostinger y la CI instalan con `pip install --require-hashes --only-binary :all:`: un paquete alterado se rechaza y no se ejecuta código de compilación de terceros |
| Vulnerabilidades | `pip-audit` sobre el lock y sobre el entorno instalado; `npm audit` sobre `package-lock.json` y sobre `scripts/vendor-audit/` (paquetes cuyos archivos se copian en `static/vendor`) |
| Recursos de terceros en el navegador | Motor OCR y fuente Ubuntu Sans alojados en `static/`, versiones fijadas y SHA-256 en `static/vendor/SHA256SUMS`. `server.js` los verifica al arrancar en Hostinger y **no arranca** si alguno cambió |
| GitHub | `.github/workflows/ci.yml`: auditoría + pruebas contra MySQL 8.4 y MariaDB 11.4 en cada push, PR y **cada lunes** (detecta avisos nuevos). `dependabot.yml` propone actualizaciones (pip, npm, Actions) para revisión humana. Recomendado: proteger `main` exigiendo ambos trabajos |
| Ejecución | `npm run audit:security` o `bash scripts/security_audit.sh` (requiere `requirements-dev.txt`) |

Resultado al 2026-10-19: **sin vulnerabilidades conocidas** (31 paquetes Python, 0 en npm) y 14 archivos de terceros íntegros. Se descartaron versiones publicadas hace menos de dos semanas (p. ej. Pillow 13.0.0, del 2026-10-16).
