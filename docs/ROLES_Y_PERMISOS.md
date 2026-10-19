# Roles y permisos

Los permisos se guardan en MySQL (`roles`, `permissions`, `role_permissions`) y se comprueban **en el servidor** en cada solicitud. Ocultar un botón solo mejora la interfaz; no es la barrera.

## Perfiles

| Código | Nombre visible (configurable) | Descripción |
|---|---|---|
| `RESPONSABLE` | «Responsable administrativo» (provisional) | Propietario funcional. Todos los permisos |
| `JEFE` | «Jefe» | Igual que el responsable, salvo la gestión de usuarios |
| `TECNICO` | «Técnico» | Inicio y Creación de informe; solo sus propios informes; edita mientras el informe está abierto y pide permiso para reabrirlo |

El nombre visible se cambia sin tocar el código, desde **Usuarios → Nombres visibles** o con `flask --app wsgi set-role-name RESPONSABLE "Nuevo nombre"`. Ambas vías quedan auditadas.

## Matriz

| Permiso | Responsable | Jefe | Técnico |
|---|:-:|:-:|:-:|
| `module.home` · Inicio | ✔ | ✔ | ✔ |
| `module.reports` · Creación de informe | ✔ | ✔ | ✔ |
| `module.consolidation` · Consolidación | ✔ | ✔ | ✘ |
| `module.audit_log` · Registro detallado | ✔ | ✔ | ✘ |
| `report.create` | ✔ | ✔ | ✔ |
| `report.edit_own` · editar, cerrar y enviar los propios | ✔ | ✔ | ✔ |
| `report.view_sent` · ver informes enviados por otros | ✔ | ✔ | ✘ |
| `report.view_all` · ver y editar **todos** los informes, también los no enviados | ✔ | ✔ | ✘ |
| `report.request_reopen` · solicitar la reapertura de un informe propio cerrado | ✔ | ✔ | ✔ |
| `report.import` · crear informes desde una matriz Excel/CSV | ✔ | ✔ | ✔ |
| `report.edit_any` · editar, cerrar y enviar informes visibles de otros | ✔ | ✔ | ✘ |
| `report.reopen` | ✔ | ✔ | ✘ |
| `report.download` | ✔ | ✔ | ✘ |
| `audit.read` / `audit.verify` · incluye **Entradas y salidas de sesión** (usuario, entrada, salida, IP, sistema operativo) | ✔ | ✔ | ✘ |
| `users.manage` | ✔ | ✘ | ✘ |

Ningún perfil puede modificar ni eliminar registros de auditoría: no existe la ruta y la cuenta de base de datos de la aplicación carece de `UPDATE`/`DELETE` sobre esas tablas.

## Reglas por recurso (informe)

- **Visibilidad** (se aplica en el `WHERE` de todas las consultas):
  - Técnico: solo los informes de los que es autor.
  - Jefe y responsable: **todos** (`report.view_all`, decisión del propietario del 2026-10-19).
- Un informe ajeno o no visible responde **404** (no revela si existe) y queda registrado como `ACCESO_DENEGADO`.
- La **edición** solo es posible en estado EN_ELABORACION o REABIERTO, por el autor (`report.edit_own`) o por un perfil con `report.edit_any`.
- El **envío** solo es posible en estado CERRADO.
- La **reapertura** requiere `report.reopen`, un estado CERRADO o ENVIADO y un motivo.
- **Técnico:** «Guardar cambios» conserva el informe abierto y puede seguir editándolo. «Cerrar informe» lo bloquea (también en la base de datos, incluidos sus adjuntos). Para volver a editar pulsa **«Solicitar permiso para editar»** con un motivo; la solicitud aparece en el Inicio del jefe y del responsable, que la **aprueban** (reabre el informe) o la **rechazan** con una nota. Todo queda en la trazabilidad y en la auditoría. Solo puede haber una solicitud pendiente por informe.
- **Jefe y responsable:** editan cualquier informe abierto; si está cerrado, lo reabren con motivo y lo editan.
- Los informes se listan en **orden cronológico** y la Consolidación permite ordenarlos por **marca y número de serie**.

## Decisiones de interpretación (confirmar con el propietario)

1. ~~Los informes nunca enviados no eran visibles para el jefe ni para el responsable.~~ Cambiado el 2026-10-19: el jefe y el responsable ven y editan todos los informes.
2. Tras una reapertura, el **técnico autor** puede volver a editar, cerrar y enviar su informe («se permitirá la edición conforme a los permisos del usuario»).
3. «Pendientes de revisión» se interpreta, por ahora, como los informes enviados alguna vez que están reabiertos, o cerrados sin reenviar. No existe un estado formal de «revisado»; si se necesita, debe definirse como regla de negocio nueva.
4. La gestión de usuarios (altas, roles, activación, restablecimiento de contraseña) corresponde solo al responsable administrativo.
5. Cambiar el rol, desactivar una cuenta o restablecer su contraseña **revoca las sesiones abiertas** de ese usuario. No se puede desactivar ni degradar al último responsable activo.
