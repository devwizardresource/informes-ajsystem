-- AJ System · Migración 0002 · Datos base (roles, permisos, estados, cabeza de auditoría)

INSERT INTO roles (id, code, display_name, description) VALUES
    (1, 'RESPONSABLE', 'Responsable administrativo', 'Propietario funcional del sistema. Nombre visible configurable.'),
    (2, 'JEFE',        'Jefe',                       'Consulta y gestión de informes, sin administración de usuarios.'),
    (3, 'TECNICO',     'Técnico',                    'Crea, cierra y envía sus propios informes.');

INSERT INTO permissions (id, code, description) VALUES
    (1,  'module.home',          'Acceso al módulo Inicio'),
    (2,  'module.reports',       'Acceso al módulo Creación de informe'),
    (3,  'module.consolidation', 'Acceso al módulo Consolidación de informes'),
    (4,  'module.audit_log',     'Acceso al módulo Registro detallado'),
    (5,  'report.create',        'Crear informes'),
    (6,  'report.edit_own',      'Editar, cerrar y enviar informes propios abiertos'),
    (7,  'report.view_sent',     'Consultar informes enviados por otros usuarios'),
    (8,  'report.edit_any',      'Editar, cerrar y enviar informes visibles de otros usuarios'),
    (9,  'report.reopen',        'Reabrir informes cerrados o enviados'),
    (10, 'report.download',      'Descargar informes en PDF'),
    (11, 'audit.read',           'Consultar el registro de auditoría'),
    (12, 'audit.verify',         'Ejecutar la verificación de integridad de la auditoría'),
    (13, 'users.manage',         'Gestionar usuarios, roles y estado de las cuentas');

-- Responsable administrativo: todos los permisos
INSERT INTO role_permissions (role_id, permission_id) SELECT 1, id FROM permissions;
-- Jefe: todos excepto la gestión de usuarios
INSERT INTO role_permissions (role_id, permission_id) SELECT 2, id FROM permissions WHERE code <> 'users.manage';
-- Técnico: Inicio, Creación de informe, crear y gestionar los propios
INSERT INTO role_permissions (role_id, permission_id)
    SELECT 3, id FROM permissions WHERE code IN ('module.home', 'module.reports', 'report.create', 'report.edit_own');

INSERT INTO report_statuses (code, label, is_editable, sort_order) VALUES
    ('EN_ELABORACION', 'En elaboración', 1, 1),
    ('CERRADO',        'Cerrado',        0, 2),
    ('ENVIADO',        'Enviado',        0, 3),
    ('REABIERTO',      'Reabierto',      1, 4);

-- Génesis de la cadena de auditoría
INSERT INTO audit_chain_head (id, last_seq, last_hash)
    VALUES (1, 0, '0000000000000000000000000000000000000000000000000000000000000000');

INSERT INTO system_settings (setting_key, setting_value, updated_at) VALUES
    ('retention_policy', 'Conservación indefinida. No se elimina ningún registro sin decisión expresa y política aprobada.', UTC_TIMESTAMP(6));
