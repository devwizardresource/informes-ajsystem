# Pruebas

## Cómo ejecutarlas

```bash
.venv/bin/pytest          # requiere MySQL accesible y AJS_DB_ROOT_PASSWORD en .env
```

Las pruebas usan **MySQL real** (no simulado). Crean una base aislada `ajsys_test` con su propia cuenta de aplicación de privilegios mínimos y aplican las mismas migraciones y GRANT que producción.

## Resultado de la última ejecución

- **Fecha:** 2026-10-19 · **Entorno:** Ubuntu 24.04, Python 3.12.3, MySQL 8.4 (Docker)
- **Resultado:** `72 passed in 28.63s`. 0 fallidas, 0 omitidas.
- Aviso no bloqueante: WeasyPrint recomienda instalar `libharfbuzz-subset` (ver PLANTILLA_PDF.md).
- Las líneas `ERROR ... Error no controlado` de la salida corresponden a los **fallos simulados a propósito** en las pruebas de reversión.

## Cobertura de los escenarios obligatorios

| Escenario requerido | Prueba(s) |
|---|---|
| Inicio de sesión correcto | `test_auth::test_login_correcto_registra_ip_y_so` |
| Contraseña incorrecta | `test_contrasena_incorrecta`, `test_usuario_inexistente_mismo_mensaje` |
| Sesión expirada | `test_sesion_expirada_por_inactividad`, `test_sesion_expirada_absoluta` |
| Acceso a módulos no autorizados | `test_permissions::test_tecnico_no_accede_a_modulos_restringidos[...]`, `test_jefe_no_gestiona_usuarios` |
| Descarga como técnico | `test_tecnico_no_puede_descargar` |
| Registro detallado como técnico | `test_tecnico_no_accede_a_modulos_restringidos[/registro]` |
| Reapertura como técnico | `test_tecnico_no_puede_reabrir` |
| Acceso directo a rutas protegidas | `test_rutas_protegidas_sin_sesion`, `test_tecnico_no_ve_informes_ajenos` |
| Crear, guardar, recuperar y editar | `test_lifecycle::test_crear_guardar_recuperar_y_editar` |
| Cerrar y bloquear la edición del técnico | `test_cierre_bloquea_edicion_del_tecnico` |
| Modificación por solicitud directa | `test_cierre_bloquea_edicion_del_tecnico` (POST manual), `test_db_bloquea_contenido_de_informe_cerrado` (SQL directo) |
| Enviar un informe cerrado / rechazar un informe abierto | `test_envio_y_visibilidad_en_paneles`, `test_rechaza_envio_de_informe_abierto` |
| Aparición en los paneles de los destinatarios | `test_envio_y_visibilidad_en_paneles` |
| Envíos duplicados | `test_envio_duplicado` |
| Reapertura como jefe y como responsable | `test_reapertura_por_jefe_y_nuevo_cierre_conserva_historial`, `test_reapertura_por_responsable_y_edicion_directiva` |
| Nuevo cierre que conserva el historial | `test_reapertura_por_jefe_y_nuevo_cierre_conserva_historial` |
| Registro de inicio y cierre de sesión e intentos fallidos | `test_login_correcto...`, `test_cierre_de_sesion`, `test_contrasena_incorrecta`, `test_limite_de_intentos` |
| IP y sistema operativo reportado | `test_login_correcto_registra_ip_y_so` |
| Operaciones sobre informes auditadas | `test_audit::test_operaciones_quedan_auditadas` |
| Ningún perfil modifica registros desde la aplicación | `test_registro_detallado_es_de_solo_lectura` (no existe la ruta) |
| Restricciones SQL de UPDATE/DELETE | `test_cuenta_de_aplicacion_no_puede_alterar_historial[...]`, `test_disparadores_bloquean_incluso_a_cuentas_privilegiadas` |
| Detección de alteraciones | `test_detecta_alteracion_de_contenido`, `test_detecta_eliminacion_del_ultimo_registro`, `test_clave_hmac_distinta_no_valida_la_cadena` |
| Reinicio con datos disponibles | `test_persistence::test_datos_persisten_tras_reiniciar_la_aplicacion` (en un proceso Python nuevo) |
| Transacciones y reversiones | `test_reversion_si_falla_la_auditoria_durante_el_cierre`, `test_fallo_en_envio_mantiene_cerrado_y_permite_reintentar` |
| Copias y restauración | `test_copia_y_restauracion`, `test_restauracion_rechaza_copia_alterada`, `test_restauracion_no_sobrescribe_base_activa` |
| Relaciones y restricciones | `test_restricciones_de_integridad`, `test_numero_de_serie_no_es_unico` |

Pruebas adicionales de seguridad: CSRF, `Origin` externo, fijación de sesión, cambio obligatorio de contraseña, cabeceras, errores sin detalles internos, ausencia de contraseñas en la auditoría, redirección abierta, XSS, comodines `LIKE`, configuración insegura, revocación de sesiones al cambiar el rol y cuentas desactivadas.

## No cubierto por pruebas automatizadas

- Comportamiento visual en navegadores reales y móviles: la interfaz se revisó por HTML y CSS, no con un navegador automatizado.
- El JavaScript de doble clic y de confirmaciones (la protección real contra duplicados está en el servidor y sí se prueba).
- La carga y la concurrencia con muchos usuarios simultáneos.
- El despliegue en el alojamiento definitivo (pendiente de decisión).
