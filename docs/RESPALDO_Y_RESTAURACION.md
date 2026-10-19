# Respaldo y restauración

## Copia de seguridad

```bash
.venv/bin/python scripts/backup.py            # usa AJS_DB_ADMIN_USER / AJS_DB_ADMIN_PASSWORD
```

El resultado queda en `AJS_BACKUP_DIR` (directorio con permisos 0700):

| Archivo | Contenido |
|---|---|
| `ajsystem_<fecha>Z.sql.gz` | Volcado `mysqldump --single-transaction --routines --triggers` comprimido (0600) |
| `ajsystem_<fecha>Z.sha256` | Suma SHA-256 de la copia |
| `ajsystem_<fecha>Z.manifest.json` | Filas por tabla y cabeza de la cadena de auditoría en el momento de la copia |

`--single-transaction` produce una instantánea coherente sin bloquear la aplicación (tablas InnoDB).

### Política recomendada (requiere la decisión del propietario)

- **Frecuencia:** diaria (cron) y siempre **antes de cada migración o despliegue**.
- **Retención:** 30 copias diarias y 12 mensuales.
- **Ubicación:** fuera del servidor, en un almacenamiento distinto, con acceso restringido. **Cifrado en reposo**: el script no cifra; use un volumen cifrado o cifre con una herramienta del sistema autorizada (p. ej. `gpg`) antes de copiar fuera.
- **Prueba de restauración:** al menos mensual, en una base de prueba (procedimiento siguiente).
- La clave `AJS_AUDIT_HMAC_KEY` debe respaldarse por separado: sin ella no se puede verificar la cadena restaurada.

## Restauración (siempre primero en una base de prueba)

```bash
.venv/bin/python scripts/restore.py backups/ajsystem_<fecha>Z.sql.gz --target-db ajsystem_restore_prueba --apply-grants
```

El script:
1. Comprueba la suma SHA-256 contra el manifiesto. Si la copia está dañada o alterada, la rechaza.
2. Se niega a escribir sobre la base activa o sobre una base con tablas (salvo `--allow-live`).
3. Restaura con la cuenta DBA (los disparadores requieren SUPER si el binlog está activo).
4. Verifica: filas por tabla igual al manifiesto, cadena de auditoría íntegra y cabeza igual a la del manifiesto.
5. Termina con `RESTAURACIÓN VERIFICADA` (código 0) o con un informe de diferencias (código 1).

### Recuperación ante desastre en producción

1. Detenga la aplicación (o actívele el modo de mantenimiento del servidor web).
2. Haga una copia del estado actual, aunque esté dañado, para el análisis posterior.
3. Restaure en una base nueva (`ajsystem_recuperada`) y revise el resultado de la verificación.
4. Apunte `AJS_DB_NAME` a la base recuperada (o renómbrela, según la decisión del DBA) y aplique los privilegios con `--apply-grants`.
5. Arranque la aplicación, ejecute `flask --app wsgi verify-audit` y registre el incidente.

## Pruebas realizadas

- `tests/test_persistence.py::test_copia_y_restauracion`: crea datos, copia, restaura en otra base y verifica filas, cadena, cabeza, contenido de un informe y los **8 disparadores** restaurados. **Aprobada.**
- `test_restauracion_rechaza_copia_alterada` y `test_restauracion_no_sobrescribe_base_activa`. **Aprobadas.**
- Ejecución manual sobre la base de desarrollo el 2026-10-19: `RESTAURACIÓN VERIFICADA`.
