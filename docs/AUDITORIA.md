# Registro de auditoría

## Qué se registra

La escritura pasa por una sola función (`ajsystem/audit.py`: `write()` dentro de una transacción y `write_standalone()` en una transacción propia).

| Evento | Cuándo |
|---|---|
| `LOGIN_EXITOSO`, `LOGIN_FALLIDO`, `LOGIN_BLOQUEADO` | Inicio de sesión (el fallo indica el motivo internamente; el usuario solo ve un mensaje genérico) |
| `LOGOUT`, `SESION_EXPIRADA` | Cierre de sesión, caducidad por inactividad o absoluta, cuenta desactivada |
| `CONTRASENA_CAMBIADA`, `USUARIO_CONTRASENA_RESTABLECIDA` | Cambios de credenciales (sin la contraseña) |
| `INFORME_CREADO`, `INFORME_GUARDADO`, `INFORME_CERRADO`, `INFORME_ENVIADO`, `INFORME_REABIERTO`, `INFORME_DESCARGADO` | Operaciones sobre informes (la reapertura incluye el estado anterior, el nuevo y el motivo) |
| `OPERACION_INFORME_RECHAZADA` | Intento de editar un informe cerrado, de enviarlo dos veces, etc. |
| `ACCESO_DENEGADO`, `CSRF_INVALIDO` | Intentos de acceso no autorizado |
| `USUARIO_CREADO`, `PERMISOS_ROL_CAMBIADO`, `USUARIO_ESTADO_CAMBIADO` | Operaciones administrativas (web o consola) |
| `AUDITORIA_VERIFICADA`, `ERROR_SEGURIDAD` | Verificaciones de integridad y errores no controlados |

Campos de cada registro: `seq` (secuencia), `event_uuid`, `occurred_at` (hora del servidor en UTC, en microsegundos), `event_type`, `outcome` (EXITO, FALLO, DENEGADO o ERROR), `user_id`, `username`, `role_code`, `session_public_id` (identificador **no secreto** de la sesión), `ip` (observada por el servidor), `os_reported` (deducido del User-Agent, **no verificable**), `user_agent`, `object_type`, `object_id`, `detail` (JSON), `prev_hash` y `row_hash`.

**Nunca** se registran contraseñas, tokens, cookies ni el token CSRF: `_sanitize()` descarta las claves sospechosas y hay pruebas que lo comprueban. **No** se hace geolocalización.

## Controles de integridad

1. **Privilegios SQL**: la cuenta de la aplicación solo tiene `SELECT, INSERT` sobre `audit_log`, `report_versions` y `report_events` (prueba `test_cuenta_de_aplicacion_no_puede_alterar_historial`: UPDATE, DELETE, TRUNCATE y DROP TRIGGER rechazados con el error 1142/1227).
2. **Disparadores** `BEFORE UPDATE/DELETE` que rechazan la operación incluso para cuentas privilegiadas que no los desactiven (prueba con root: error 1644).
3. **Cadena HMAC-SHA256**: `row_hash = HMAC(AJS_AUDIT_HMAC_KEY, campos + seq + prev_hash)`. La fila `audit_chain_head` (bloqueada con `FOR UPDATE`) serializa las inserciones y guarda el último eslabón.
4. **Detección**: `flask --app wsgi verify-audit` o el botón «Verificar integridad» del Registro detallado recalculan la cadena y detectan:
   - contenido alterado (HMAC no coincide),
   - registros eliminados o insertados (secuencia discontinua o eslabón roto),
   - eliminación de los últimos registros (la cabeza no coincide).
   Las pruebas simulan a un DBA que desactiva los disparadores, altera o borra filas, y comprueban que se detecta.

## Alcance real y riesgos residuales

- El registro **no es absolutamente inalterable**. Un DBA o un atacante con acceso de superusuario al servidor MySQL puede desactivar los disparadores y modificar la tabla. La cadena permite **detectarlo**, siempre que no tenga además la clave HMAC.
- La clave HMAC reside en el entorno del servidor de aplicación. Quien controle ese servidor **y** la base podría reescribir la cadena completa. Mitigaciones recomendadas: guardar fuera del servidor (correo o almacenamiento de solo escritura) el `last_seq`/`last_hash` de cada verificación periódica, y conservar copias de seguridad antiguas para compararlas.
- El sistema operativo y el User-Agent los declara el cliente y pueden falsearse.
- Detrás de un proxy, la IP real solo es fiable si `AJS_TRUST_PROXY_HOPS` coincide con la infraestructura.
- Si la clave HMAC se pierde, no podrá verificarse la cadena histórica. Respáldela por separado y de forma segura. Rotarla requiere un procedimiento (pendiente): registrar un evento de rotación y verificar por tramos.

## Política de retención

Conservación **indefinida** (`system_settings.retention_policy`). No existe ningún proceso que elimine registros, ni la cuenta de la aplicación tiene permiso para hacerlo. Cualquier depuración futura requiere una decisión expresa del propietario, una política aprobada y un procedimiento ejecutado por un DBA, con respaldo previo y un registro del acto.

## Verificación periódica recomendada

Programe (cron del sistema) `flask --app wsgi verify-audit` a diario: devuelve un código distinto de 0 si detecta incidencias. Conserve su salida fuera del servidor.
