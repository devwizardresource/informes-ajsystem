-- AJ System · Privilegios mínimos de la cuenta de la aplicación.
-- Marcadores sustituidos por ajsystem/dbadmin.py: {db}, {app_user}, {app_host}
-- La cuenta de la aplicación NO tiene privilegios DDL, DELETE, TRIGGER ni GRANT.
-- Las tablas de auditoría e historial solo admiten SELECT e INSERT.

REVOKE ALL PRIVILEGES, GRANT OPTION FROM '{app_user}'@'{app_host}';

GRANT SELECT ON `{db}`.roles             TO '{app_user}'@'{app_host}';
GRANT UPDATE (display_name) ON `{db}`.roles TO '{app_user}'@'{app_host}';
GRANT SELECT ON `{db}`.permissions       TO '{app_user}'@'{app_host}';
GRANT SELECT ON `{db}`.role_permissions  TO '{app_user}'@'{app_host}';
GRANT SELECT ON `{db}`.report_statuses   TO '{app_user}'@'{app_host}';
GRANT SELECT ON `{db}`.schema_migrations TO '{app_user}'@'{app_host}';

GRANT SELECT, INSERT, UPDATE ON `{db}`.users            TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.sessions         TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.reports          TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.report_counters  TO '{app_user}'@'{app_host}';
GRANT SELECT, UPDATE         ON `{db}`.audit_chain_head TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.system_settings  TO '{app_user}'@'{app_host}';

GRANT SELECT, INSERT ON `{db}`.report_versions TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT ON `{db}`.report_events   TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT ON `{db}`.audit_log       TO '{app_user}'@'{app_host}';

-- Migración 0003: adjuntos (solo inserción; retirada lógica y texto OCR) y solicitudes de reapertura.
GRANT SELECT, INSERT ON `{db}`.report_attachments TO '{app_user}'@'{app_host}';
GRANT UPDATE (ocr_text, deleted_at, deleted_by) ON `{db}`.report_attachments TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.reopen_requests TO '{app_user}'@'{app_host}';

-- Migración 0005: firma de perfil e invitaciones de primer acceso.
GRANT SELECT, INSERT, UPDATE ON `{db}`.user_signatures  TO '{app_user}'@'{app_host}';
GRANT SELECT, INSERT, UPDATE ON `{db}`.user_invitations TO '{app_user}'@'{app_host}';
