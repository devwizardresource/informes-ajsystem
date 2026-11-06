-- AJ System · Migración 0011 · rol Supervisor (consulta sin modificación)
--
-- Supervisa el avance de la auditoría: consulta y filtra el tablero, la consolidación y los
-- informes de todos los técnicos. No recibe ningún permiso de escritura: ni report.create,
-- report.edit_*, report.reopen, report.delete, report.sign_closed ni dashboard.executive.

INSERT INTO roles (id, code, display_name, description) VALUES
    (4, 'SUPERVISOR', 'Supervisor', 'Consulta tableros e informes para la supervisión y el control. No modifica nada.');

INSERT INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id FROM roles r JOIN permissions p
        ON p.code IN ('module.home', 'module.consolidation', 'report.view_all', 'report.download', 'dashboard.view')
     WHERE r.code = 'SUPERVISOR';
