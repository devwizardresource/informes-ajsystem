# Medidas de seguridad

| # | Requisito | Implementación | Verificación |
|---|---|---|---|
| 1 | Hash de contraseñas | Argon2id (`argon2-cffi`), rehash automático si cambian los parámetros; hash ficticio para igualar tiempos cuando el usuario no existe | `security/passwords.py` |
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
| 19 | Dependencias | Versiones fijadas en `requirements.txt`; revisión periódica con `pip list --outdated` y `pip-audit` (herramienta opcional, pendiente de autorización) | [DEPENDENCIAS.md](DEPENDENCIAS.md) |
| 20 | Pruebas antes de desplegar | `pytest` completo, incluidos los casos de seguridad | [PRUEBAS.md](PRUEBAS.md) |

Otras medidas:
- Cabeceras `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` y `Cache-Control: no-store` en las páginas autenticadas.
- Solicitudes limitadas a 1 MB.
- Sin redirecciones abiertas tras el login (prueba `test_sin_redireccion_abierta_tras_login`).
- La IP reenviada (`X-Forwarded-For`) solo se acepta si `AJS_TRUST_PROXY_HOPS > 0`.
- La generación de PDF bloquea la carga de recursos externos (`url_fetcher` limitado a la carpeta de plantillas).
- Cuenta MySQL de la aplicación con privilegios mínimos (`sql/grants_app.sql`): sin DDL, sin `DELETE`, sin `TRIGGER`.
