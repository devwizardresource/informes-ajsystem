# Roles y permisos

Los permisos se guardan en MySQL (`roles`, `permissions`, `role_permissions`) y se comprueban **en el servidor** en cada solicitud. Ocultar un botón solo mejora la interfaz; no es la barrera.

## Perfiles

| Código | Nombre visible (configurable) | Descripción |
|---|---|---|
| `RESPONSABLE` | «Responsable administrativo» (provisional) | Propietario funcional. Todos los permisos |
| `JEFE` | «Jefe» | Igual que el responsable, salvo la gestión de usuarios |
| `TECNICO` | «Técnico» | Inicio, Creación y Consolidación; consulta, reabre con motivo y edita todos los informes |

El nombre visible se cambia sin tocar el código, desde **Usuarios → Nombres visibles** o con `flask --app wsgi set-role-name RESPONSABLE "Nuevo nombre"`. Ambas vías quedan auditadas.

## Matriz

| Permiso | Responsable | Jefe | Técnico |
|---|:-:|:-:|:-:|
| `module.home` · Inicio | ✔ | ✔ | ✔ |
| `module.reports` · Creación de informe | ✔ | ✔ | ✔ |
| `module.consolidation` · Consolidación | ✔ | ✔ | ✔ |
| `module.audit_log` · Registro detallado | ✔ | ✔ | ✘ |
| `report.create` | ✔ | ✔ | ✔ |
| `report.edit_own` · editar, cerrar y enviar los propios | ✔ | ✔ | ✔ |
| `report.view_sent` · ver informes enviados por otros | ✔ | ✔ | ✘ |
| `report.view_all` · ver **todos** los informes, también los no enviados | ✔ | ✔ | ✔ |
| `report.request_reopen` · flujo anterior de solicitud (sin perfiles asignados) | ✘ | ✘ | ✘ |
| `report.import` · crear informes desde una matriz Excel/CSV | ✔ | ✔ | ✔ |
| `report.edit_any` · editar, cerrar y enviar informes visibles de otros | ✔ | ✔ | ✔ |
| `report.reopen` · reabrir un informe cerrado con motivo | ✔ | ✔ | ✔ |
| `report.download` | ✔ | ✔ | ✘ |
| `audit.read` / `audit.verify` · incluye **Entradas y salidas de sesión** (usuario, entrada, salida, IP, sistema operativo) | ✔ | ✔ | ✘ |
| `report.delete` · eliminar y restaurar informes (con motivo, auditado) | ✔ | ✘ | ✘ |
| `users.manage` | ✔ | ✘ | ✘ |

Ningún perfil puede modificar ni eliminar registros de auditoría: no existe la ruta y la cuenta de base de datos de la aplicación carece de `UPDATE`/`DELETE` sobre esas tablas.

## Reglas por recurso (informe)

- **Visibilidad** (se aplica en el `WHERE` de todas las consultas):
  - Todos los perfiles activos: **todos** (`report.view_all`, decisión del propietario del 2026-10-20).
- Un informe ajeno o no visible responde **404** (no revela si existe) y queda registrado como `ACCESO_DENEGADO`.
- La **edición** solo es posible en estado EN_ELABORACION o REABIERTO; cualquier perfil puede editar cualquier informe (`report.edit_any`).
- El **envío** solo es posible en estado CERRADO.
- La **reapertura** requiere `report.reopen`, un estado CERRADO o ENVIADO y un motivo. Todos los perfiles pueden reabrir sin aprobación de otra persona; la operación se conserva en la trazabilidad y la auditoría.
- «Guardar cambios» conserva el informe abierto. «Cerrar informe» lo bloquea (también en la base de datos, incluidos sus adjuntos). Para volver a editarlo, cualquier usuario puede usar **«Reabrir para editar»** con un motivo y continuar.
- Los informes se listan en **orden cronológico** y la Consolidación permite ordenarlos por **marca y número de serie**.

## Decisiones de interpretación (confirmar con el propietario)

1. Desde el 2026-10-20 cualquier perfil consulta y edita todos los informes; reabrir un informe cerrado exige un motivo y queda auditado, pero no aprobación de jefe o responsable.
2. «Pendientes de revisión» se interpreta, por ahora, como los informes enviados alguna vez que están reabiertos, o cerrados sin reenviar. No existe un estado formal de «revisado»; si se necesita, debe definirse como regla de negocio nueva.
3. La gestión de usuarios (altas, roles, activación, restablecimiento de contraseña e invitaciones) corresponde solo al responsable administrativo.
4. Cambiar el rol, desactivar una cuenta o restablecer su contraseña **revoca las sesiones abiertas** de ese usuario. No se puede desactivar ni degradar al último responsable activo.
5. El técnico puede cargar su firma desde **Mi perfil**. El responsable también puede cargar o reemplazar la firma de las cuentas con rol Técnico desde **Usuarios**; la imagen se copia a los informes nuevos al cerrarlos y se muestra en «Firma digital de quien elabora». Solo el jefe y el responsable pueden cargar o retirar «Firma digital de quien recibe».

Las invitaciones de primer acceso se preparan en **Usuarios → Invitar por WhatsApp**. El responsable debe registrar el número internacional; el sistema abre WhatsApp Web con el mensaje y un enlace de un solo uso que vence en 72 horas. El responsable pulsa Enviar en WhatsApp.

## Eliminación de informes (responsable administrativo)

El responsable (admin) elimina un informe desde su página («Eliminar informe», motivo obligatorio). Desaparece de todas las vistas y consultas para todos los perfiles, también para su autor. La eliminación es **lógica**: el historial, las fotos y el registro de auditoría se conservan, porque borrar filas rompería la cadena de integridad HMAC. Desde **Inicio → Informes eliminados** (o Consolidación) puede consultarlos y **restaurarlos**. Eliminar y restaurar quedan en el registro detallado (`INFORME_ELIMINADO`, `INFORME_RESTAURADO`).

Los eventos del registro detallado (auditoría) **no** se pueden eliminar desde la aplicación: es la garantía de que el registro de accesos es fiable.
