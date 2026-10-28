# Roles y permisos

Los permisos se guardan en MySQL (`roles`, `permissions`, `role_permissions`) y se comprueban **en el servidor** en cada solicitud. Ocultar un botón solo mejora la interfaz; no es la barrera.

## Perfiles

| Código | Nombre visible (configurable) | Descripción |
|---|---|---|
| `RESPONSABLE` | «Director» (desde la migración 0007) | Propietario funcional. Todos los permisos; firma los informes cerrados y consulta el tablero ejecutivo |
| `JEFE` | «Jefe» | Igual que el responsable, salvo la gestión de usuarios |
| `TECNICO` | «Técnico» | Inicio y Creación de informe; solo ve, edita, cierra y reabre **sus propios** informes (migración 0008) |

El nombre visible se cambia sin tocar el código, desde **Usuarios → Nombres visibles** o con `flask --app wsgi set-role-name RESPONSABLE "Nuevo nombre"`. Ambas vías quedan auditadas.

## Matriz

| Permiso | Responsable | Jefe | Técnico |
|---|:-:|:-:|:-:|
| `module.home` · Inicio | ✔ | ✔ | ✔ |
| `module.reports` · Creación de informe | ✔ | ✔ | ✔ |
| `module.consolidation` · Consolidación | ✔ | ✔ | ✘ |
| `module.audit_log` · Registro detallado | ✔ | ✔ | ✘ |
| `report.create` | ✔ | ✔ | ✔ |
| `report.edit_own` · editar y cerrar los propios | ✔ | ✔ | ✔ |
| `report.view_sent` · ver informes enviados por otros (heredado) | ✔ | ✔ | ✘ |
| `report.view_all` · ver **todos** los informes | ✔ | ✔ | ✘ |
| `report.request_reopen` · flujo anterior de solicitud (sin perfiles asignados) | ✘ | ✘ | ✘ |
| `report.import` · crear informes desde una matriz Excel/CSV | ✔ | ✔ | ✔ |
| `report.edit_any` · editar y cerrar informes de otros | ✔ | ✔ | ✘ |
| `report.reopen` · reabrir con motivo un informe cerrado visible (firmado: solo el Director) | ✔ | ✔ | ✔ (los propios) |
| `report.download` | ✔ | ✔ | ✘ |
| `audit.read` / `audit.verify` · incluye **Entradas y salidas de sesión** (usuario, entrada, salida, IP, sistema operativo) | ✔ | ✔ | ✘ |
| `report.delete` · eliminar y restaurar informes (con motivo, auditado) | ✔ | ✘ | ✘ |
| `users.manage` · incluye **eliminar usuarios** (eliminación lógica) | ✔ | ✘ | ✘ |
| `report.sign_closed` · firmar como quien recibe / aprueba un informe **cerrado** | ✔ | ✘ | ✘ |
| `dashboard.executive` · **Tablero ejecutivo** (`/tablero`) | ✔ | ✘ | ✘ |
| `home.activity` · panel «Mi actividad» en Inicio (solo los informes propios) | ✘ | ✘ | ✔ |

Ningún perfil puede modificar ni eliminar registros de auditoría: no existe la ruta y la cuenta de base de datos de la aplicación carece de `UPDATE`/`DELETE` sobre esas tablas.

## Reglas por recurso (informe)

- **Visibilidad** (se aplica en el `WHERE` de todas las consultas):
  - Director y jefe: **todos** (`report.view_all`).
  - Técnico: **solo los propios** (desde la migración 0008, 2026-10-28).
- Un informe ajeno o no visible responde **404** (no revela si existe) y queda registrado como `ACCESO_DENEGADO`.
- La **edición** solo es posible en estado EN_ELABORACION o REABIERTO; el técnico edita los suyos y la dirección cualquiera (`report.edit_any`).
- **Cerrar es el último paso** (el paso «Enviar» se retiró el 2026-10-28): el informe queda bloqueado y pendiente de la firma del Director. Los informes antiguos en estado ENVIADO se muestran y se filtran como «Cerrado»; no se modificó ningún dato.
- La **reapertura** requiere `report.reopen`, un informe cerrado visible y un motivo. Si el Director ya lo firmó, solo el Director puede reabrirlo y su firma se retira (queda en el historial) para que vuelva a firmar la versión corregida.
- «Guardar cambios» conserva el informe abierto. «Cerrar informe» lo bloquea (también en la base de datos, incluidos sus adjuntos).
- Los informes se listan en **orden cronológico** y la Consolidación permite ordenarlos por **marca y número de serie**.

## Decisiones de interpretación (confirmar con el propietario)

1. Desde el 2026-10-20 cualquier perfil consulta y edita todos los informes; reabrir un informe cerrado exige un motivo y queda auditado, pero no aprobación de jefe o responsable.
2. «Reabiertos para corrección» lista los informes en estado REABIERTO. No existe un estado formal de «revisado»; si se necesita, debe definirse como regla de negocio nueva.
3. La gestión de usuarios (altas, roles, activación, restablecimiento de contraseña e invitaciones) corresponde solo al Director.
4. Cambiar el rol, desactivar, eliminar una cuenta o restablecer su contraseña **revoca las sesiones abiertas** de ese usuario. No se puede desactivar, degradar ni eliminar al último Director activo, y nadie puede eliminarse a sí mismo.
6. **Eliminar usuario** (Usuarios → ficha del usuario → escribir ELIMINAR) es lógico: la cuenta no puede iniciar sesión, desaparece de la gestión y no se puede reactivar (lo impide también la base de datos). Sus informes, firmas, versiones y auditoría se conservan a su nombre; el nombre de usuario y la identificación quedan reservados.
5. El técnico puede cargar su firma desde **Mi perfil**. El responsable también puede cargar o reemplazar la firma de las cuentas con rol Técnico desde **Usuarios**; la imagen se copia a los informes nuevos al cerrarlos y se muestra en «Firma digital de quien elabora». Solo el jefe y el responsable pueden cargar o retirar «Firma digital de quien recibe».

Las invitaciones de primer acceso se preparan en **Usuarios → Invitar por WhatsApp**. El responsable debe registrar el número internacional; el sistema abre WhatsApp Web con el mensaje y un enlace de un solo uso que vence en 72 horas. El responsable pulsa Enviar en WhatsApp.

## Firma del Director en informes cerrados

Al cerrarse, un informe aparece en **Inicio → Informes cerrados pendientes de mi firma**. El Director lo firma con la firma guardada en **Mi perfil**: pulsa **Firmar** (la firma aparece en «Firma digital de quien recibe / aprueba», todavía sin guardar) y luego **Guardar cambios**. También puede dibujarla o cargar una imagen en ese recuadro. Solo esa firma se admite con el informe cerrado: el contenido, las fotos y la firma de quien elabora siguen bloqueados, también en la base de datos (disparadores de la migración 0007). Firmar no cambia la revisión ni la huella del informe; reemplazar la firma retira la anterior de forma lógica (queda en el historial) y todo queda en la auditoría (`ADJUNTO_AGREGADO`).

## Panel de actividad y tablero ejecutivo

- **Mi actividad** (técnico, en Inicio): calendario de 53 semanas con los informes que el propio técnico creó cada día. La consulta se filtra por el usuario de la sesión; no admite parámetros.
- **Tablero ejecutivo** (`/tablero`, solo Director): KPI, informes por día y por técnico, ranking, estados, inventario (estado del equipo, dispositivo, marca) y tablas de equipos en mal estado, con observaciones y con fotografías, con filtros de fechas y técnico. Es de solo lectura. Los equipos se cuentan una vez (marca + número de serie, con su informe más reciente del periodo); «Sin dato» indica un campo no diligenciado y «Sin datos» un promedio sin base, distinto de un cero real. Los informes eliminados no cuentan.

## Eliminación de informes (Director)

El responsable (admin) elimina un informe desde su página («Eliminar informe», motivo obligatorio). Desaparece de todas las vistas y consultas para todos los perfiles, también para su autor. La eliminación es **lógica**: el historial, las fotos y el registro de auditoría se conservan, porque borrar filas rompería la cadena de integridad HMAC. Desde **Inicio → Informes eliminados** (o Consolidación) puede consultarlos y **restaurarlos**. Eliminar y restaurar quedan en el registro detallado (`INFORME_ELIMINADO`, `INFORME_RESTAURADO`).

Los eventos del registro detallado (auditoría) **no** se pueden eliminar desde la aplicación: es la garantía de que el registro de accesos es fiable.
