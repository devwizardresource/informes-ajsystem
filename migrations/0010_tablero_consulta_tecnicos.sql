-- AJ System · Migración 0010 · consulta del tablero para todos los perfiles
--
-- Ver el tablero y filtrarlo pasa a ser un permiso propio. 'dashboard.executive' queda como la
-- administración del tablero (cargar la matriz) y sigue siendo exclusivo del Director.

INSERT IGNORE INTO permissions (id, code, description) VALUES
    (21, 'dashboard.view', 'Consultar y filtrar el tablero, sin modificar nada ni cargar archivos');

UPDATE permissions
   SET description = 'Administrar el tablero: cargar la matriz de inventario'
 WHERE code = 'dashboard.executive';

INSERT IGNORE INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code = 'dashboard.view'
     WHERE r.code IN ('RESPONSABLE', 'JEFE', 'TECNICO');
