# Roles y permisos

Los permisos se guardan en MySQL (`roles`, `permissions`, `role_permissions`) y se comprueban **en el servidor** en cada solicitud. Ocultar un botón solo mejora la interfaz; no es la barrera.

## Perfiles

| Código | Nombre visible (configurable) | Descripción |
|---|---|---|
| `RESPONSABLE` | «Responsable administrativo» (provisional) | Propietario funcional. Todos los permisos |
| `JEFE` | «Jefe» | Igual que el responsable, salvo la gestión de usuarios |
| `TECNICO` | «Técnico» | Inicio y Creación de informe; solo sus propios informes |

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
| `report.edit_any` · editar, cerrar y enviar informes visibles de otros | ✔ | ✔ | ✘ |
| `report.reopen` | ✔ | ✔ | ✘ |
| `report.download` | ✔ | ✔ | ✘ |
| `audit.read` / `audit.verify` | ✔ | ✔ | ✘ |
| `users.manage` | ✔ | ✘ | ✘ |

Ningún perfil puede modificar ni eliminar registros de auditoría: no existe la ruta y la cuenta de base de datos de la aplicación carece de `UPDATE`/`DELETE` sobre esas tablas.

## Reglas por recurso (informe)

- **Visibilidad** (se aplica en el `WHERE` de todas las consultas):
  - Técnico: solo los informes de los que es autor.
  - Jefe y responsable: los suyos y los que **se enviaron al menos una vez** (`first_sent_at IS NOT NULL`).
- Un informe ajeno o no visible responde **404** (no revela si existe) y queda registrado como `ACCESO_DENEGADO`.
- La **edición** solo es posible en estado EN_ELABORACION o REABIERTO, por el autor (`report.edit_own`) o por un perfil con `report.edit_any`.
- El **envío** solo es posible en estado CERRADO.
- La **reapertura** requiere `report.reopen`, un estado CERRADO o ENVIADO y un motivo.

## Decisiones de interpretación (confirmar con el propietario)

1. Los informes **nunca enviados** de un técnico no son visibles para el jefe ni para el responsable: el encargo indica que el informe pasa a estar disponible para ellos «al enviarse».
2. Tras una reapertura, el **técnico autor** puede volver a editar, cerrar y enviar su informe («se permitirá la edición conforme a los permisos del usuario»).
3. «Pendientes de revisión» se interpreta, por ahora, como los informes enviados alguna vez que están reabiertos, o cerrados sin reenviar. No existe un estado formal de «revisado»; si se necesita, debe definirse como regla de negocio nueva.
4. La gestión de usuarios (altas, roles, activación, restablecimiento de contraseña) corresponde solo al responsable administrativo.
5. Cambiar el rol, desactivar una cuenta o restablecer su contraseña **revoca las sesiones abiertas** de ese usuario. No se puede desactivar ni degradar al último responsable activo.
