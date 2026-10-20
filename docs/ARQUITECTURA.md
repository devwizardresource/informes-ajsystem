# Arquitectura

Los diagramas usan Mermaid (se ven en GitHub y en VS Code con la extensión «Markdown Preview Mermaid Support»).

## 1. Componentes

```mermaid
flowchart LR
  subgraph Navegador
    UI[HTML + CSS + JS<br/>sin scripts en línea]
  end
  subgraph Servidor["Servidor Python (WSGI)"]
    MW[Middleware: sesión, CSRF,<br/>cabeceras, errores]
    WEB[web/*: rutas por módulo]
    AUTHZ[security/authz<br/>permisos por rol]
    SVC[reports/service<br/>máquina de estados]
    USR[users]
    AUD[audit<br/>HMAC encadenado]
    DOC[documents<br/>WeasyPrint]
  end
  subgraph MySQL["MySQL 8.4"]
    T[(tablas de negocio)]
    H[(historial y auditoría<br/>solo INSERT + disparadores)]
  end
  UI -- HTTPS --> MW --> WEB
  WEB --> AUTHZ
  WEB --> SVC & USR & DOC
  SVC --> AUD
  USR --> AUD
  SVC & USR & AUD --> T & H
```

| Capa | Responsabilidad | Archivos |
|---|---|---|
| Presentación | Formularios, tablas, paneles y navegación por perfil | `templates/`, `static/` |
| Rutas | Validan la petición y llaman a los servicios; nunca escriben SQL de negocio | `web/` |
| Negocio | Estados, permisos por recurso, versiones, usuarios, documentos | `reports/`, `users.py`, `documents.py` |
| Seguridad | Sesiones, CSRF, contraseñas, autorización, datos del cliente | `security/` |
| Persistencia | Conexión, transacciones, migraciones, respaldo | `db.py`, `migrations/`, `backup.py` |

### Consistencia entre la operación y la auditoría

Cada operación crítica (crear, guardar, cerrar, enviar, reabrir, cambiar un rol, iniciar o cerrar sesión) escribe su evento de auditoría **en la misma transacción** que los datos. Si falla la auditoría, se revierte la operación completa; si falla la operación, no queda evento. Los eventos sin transacción propia (fallos de inicio de sesión, accesos denegados, descargas) se escriben en una transacción independiente **antes** de responder. Si esa escritura falla, la solicitud termina en error (falla cerrado): por ejemplo, un PDF no se entrega sin su registro.

## 2. Ciclo de vida del informe

```mermaid
stateDiagram-v2
  [*] --> EN_ELABORACION: Crear (técnico, jefe, responsable)
  EN_ELABORACION --> EN_ELABORACION: Guardar cambios (nueva revisión)
  EN_ELABORACION --> CERRADO: Cerrar informe y registrar
  CERRADO --> ENVIADO: Enviar informe
  CERRADO --> REABIERTO: Reabrir (cualquier usuario, con motivo)
  ENVIADO --> REABIERTO: Reabrir (cualquier usuario, con motivo)
  REABIERTO --> REABIERTO: Guardar cambios
  REABIERTO --> CERRADO: Cerrar informe y registrar
```

- **Guardar** nunca cierra ni envía. Cada guardado con cambios crea una revisión en `report_versions`.
- **Cerrar** valida los campos obligatorios, persiste los datos definitivos y bloquea el contenido.
- **Enviar** es una operación distinta: solo es posible desde CERRADO y es idempotente mediante `op_id`.
- **Reabrir** exige un motivo de 10 caracteres como mínimo. No borra versiones ni eventos anteriores.
- La transición se valida tres veces: en Python (`reports/service.py`), en el `UPDATE ... WHERE status_code = <anterior>` y en el disparador `trg_reports_state_guard`, que además impide modificar el contenido de un informe CERRADO o ENVIADO.
- El bloqueo de fila (`SELECT ... FOR UPDATE`) y la columna `revision` evitan carreras y sobrescrituras entre usuarios.

## 3. Modelo entidad-relación

```mermaid
erDiagram
  roles ||--o{ users : "tiene"
  roles ||--o{ role_permissions : ""
  permissions ||--o{ role_permissions : ""
  users ||--o{ sessions : "abre"
  users ||--o{ reports : "es autor de"
  report_statuses ||--o{ reports : "estado"
  reports ||--o{ report_versions : "versiones"
  reports ||--o{ report_events : "operaciones"
  users ||--o{ report_events : "ejecuta"
  users ||--o{ audit_log : "actor (opcional)"
  audit_chain_head ||--|| audit_log : "último eslabón"

  users { bigint id PK
          varchar username UK
          varchar password_hash
          tinyint role_id FK
          bool is_active
          bool must_change_password }
  sessions { bigint id PK
             char public_id UK
             char token_hash UK
             bigint user_id FK
             char csrf_token
             datetime absolute_expires_at
             enum end_reason }
  reports { bigint id PK
            varchar code UK
            bigint owner_id FK
            varchar status_code FK
            varchar template_code
            varchar equipment_serial "indexado, NO único"
            json data
            int revision
            datetime closed_at
            datetime first_sent_at }
  report_versions { bigint id PK
                    bigint report_id FK
                    int revision "UK con report_id"
                    json data
                    char content_sha256 }
  report_events { bigint id PK
                  bigint report_id FK
                  enum action
                  varchar from_status
                  varchar to_status
                  varchar reason
                  char idempotency_key "UK con report_id+action" }
  audit_log { bigint id PK
              bigint seq UK
              varchar event_type
              enum outcome
              varchar ip
              varchar os_reported
              json detail
              char prev_hash
              char row_hash }
```

Tablas adicionales: `report_counters` (secuencia anual de identificadores), `system_settings` y `schema_migrations`.
Migración 0003: `report_attachments` (fotos y firmas por informe, LONGBLOB, solo inserción con retirada lógica; bloqueadas con el informe cerrado), `reopen_requests` (solicitudes de reapertura, como máximo una pendiente por informe), `users.id_number` (sustituye al correo; único y válido para iniciar sesión) y `reports.equipment_brand` (índice marca + serie).
Migración 0004: `reports.deleted_at/deleted_by/deletion_reason` (eliminación lógica por el responsable; los informes eliminados se excluyen en `visibility_clause`).
Migración 0005: teléfonos para WhatsApp, firmas persistentes del perfil, enlaces de invitación de un solo uso (72 horas) y permisos de consulta/edición/reapertura para todos los perfiles.
El número de serie del equipo **no** identifica al informe: un mismo equipo puede auditarse varias veces (comprobado por una prueba).

## 4. Rutas principales

| Ruta | Método | Permiso |
|---|---|---|
| `/login`, `/logout`, `/cuenta/contrasena` | GET/POST | — / sesión |
| `/cuenta/perfil`, `/cuenta/perfil/firma` | GET / POST | sesión propia |
| `/invitacion/<token>` | GET / POST | token de primer acceso válido |
| `/` (Inicio) | GET | `module.home` |
| `/informes`, `/informes/nuevo` | GET / POST | `module.reports`, `report.create` |
| `/informes/<id>` | GET | módulo de informes + visibilidad del recurso |
| `/informes/<id>/guardar` (accion=guardar\|cerrar) | POST | capacidad `edit`/`close` del recurso |
| `/informes/<id>/enviar` | POST | capacidad `send` |
| `/informes/<id>/reabrir` | POST | `report.reopen` |
| `/informes/<id>/solicitar-reapertura` | POST | `report.request_reopen` (autor) |
| `/informes/<id>/resolver-solicitud` | POST | `report.reopen` |
| `/informes/<id>/adjuntos[/<id>[/retirar]]` | POST / GET | capacidad `edit` / visibilidad |
| `/informes/<id>/autocompletar` | POST | capacidad `edit` (texto OCR → campos) |
| `/informes/importar`, `/informes/importar/plantilla.xlsx` | POST / GET | `report.create`, `report.import` |
| `/informes/<id>/eliminar`, `/informes/<id>/restaurar`, `/informes/eliminados` | POST / GET | `report.delete` |
| `/informes/plantilla.css` | GET | — (CSS de `resources/Plantilla_Informe_AJ_System.html`) |
| `/informes/<id>/pdf` | GET | `report.download` + visibilidad |
| `/consolidacion` | GET | `module.consolidation`, `report.view_sent` |
| `/registro`, `/registro/sesiones`, `/registro/verificar` | GET / POST | `module.audit_log`, `audit.read` / `audit.verify` |
| `/administracion/...` | GET/POST | `users.manage` |
| `/administracion/usuarios/<id>/firma` | POST | `users.manage`; solo cuentas con rol `TECNICO` |
