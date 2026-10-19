# Pruebas

## Cómo ejecutarlas

```bash
.venv/bin/pytest          # requiere MySQL accesible y AJS_DB_ROOT_PASSWORD en .env
```

Las pruebas usan **MySQL real** (no simulado). Crean una base aislada `ajsys_test` con su propia cuenta de aplicación de privilegios mínimos y aplican las mismas migraciones y GRANT que producción.

## Resultado de la última ejecución

- **Fecha:** 2026-10-19 · **Entorno:** Ubuntu 24.04, Python 3.12.3
- **MySQL 8.4:** `96 passed`. **MariaDB 11.4** (motor de Hostinger): `96 passed` (respaldos con `mariadb-dump`). 0 fallidas, 0 omitidas.
- La CI de GitHub repite ambas ejecuciones en cada push.
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
| Técnico edita tras guardar y pierde la edición al cerrar; solicitud, aprobación y rechazo de reapertura | `test_features::test_tecnico_edita_tras_guardar_y_pierde_edicion_al_cerrar`, `test_rechazo_de_solicitud_mantiene_cerrado` |
| Jefe y responsable ven y editan cualquier informe | `test_permissions::test_jefe_y_responsable_ven_y_editan_cualquier_informe[...]` |
| Registro de entradas/salidas con IP y S. O., solo jefe y responsable | `test_registro_de_sesiones_solo_jefe_y_responsable` |
| Login con usuario, nombre completo o identificación; sin correo | `test_login_con_numero_de_identificacion`, `test_usuario_e_identificacion_no_se_cruzan`, `test_sin_correo_en_usuarios` |
| Política de contraseña (minúscula, mayúscula, número, símbolo, 12+) | `test_politica_de_contrasena[...]` |
| Fotos: anexo, EXIF eliminado, privadas, archivos falsos rechazados, no borrables | `test_foto_se_anexa_sin_metadatos_y_es_privada`, `test_archivo_no_imagen_rechazado`, `test_adjuntos_no_se_borran_en_la_base` |
| Autocompletado desde el texto de la foto | `test_autocompletar_desde_texto_de_foto`, `test_extractor_de_texto[...]` |
| Importación Excel/CSV (un informe por equipo) | `test_importar_excel_crea_un_informe_por_equipo`, `test_importar_csv_y_archivo_invalido`, `test_plantilla_de_importacion_descargable` |
| PDF idéntico a la plantilla: una hoja + anexo fotográfico | `test_pdf_incluye_anexo_fotografico` |

Pruebas adicionales de seguridad: CSRF, `Origin` externo, fijación de sesión, cambio obligatorio de contraseña, cabeceras, errores sin detalles internos, ausencia de contraseñas en la auditoría, redirección abierta, XSS, comodines `LIKE`, configuración insegura, revocación de sesiones al cambiar el rol y cuentas desactivadas.

## No cubierto por pruebas automatizadas

- Recorrido en navegador real (Chrome sin interfaz, 2026-10-19, fuera de la batería pytest): login por identificación, foto → OCR → 9 campos autocompletados en ~1 s (sin errores de CSP), firma, guardar, cerrar, solicitud y aprobación de reapertura, PDF, vista móvil 390 px sin desplazamiento horizontal.
- El JavaScript de doble clic y de confirmaciones (la protección real contra duplicados está en el servidor y sí se prueba).
- La carga y la concurrencia con muchos usuarios simultáneos.
- El despliegue real en Hostinger (Python del servidor, Pango y permisos de disparadores dependen del plan).
