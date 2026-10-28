-- AJ System · Migración 0008 · técnicos solo con sus informes y eliminación de usuarios
--
-- 1) El técnico deja de ver la Consolidación y los informes de otros usuarios. La migración 0005
--    le había dado 'module.consolidation' sin 'report.view_sent', por eso la Consolidación le
--    respondía 403. Conserva la reapertura de SUS informes (no puede ver los ajenos).
-- 2) El Director puede eliminar usuarios. La eliminación es lógica: la cuenta queda inactiva y
--    oculta, y sus informes, firmas, sesiones y auditoría se conservan íntegros.

DELETE rp FROM role_permissions rp
  JOIN roles r ON r.id = rp.role_id
  JOIN permissions p ON p.id = rp.permission_id
 WHERE r.code = 'TECNICO'
   AND p.code IN ('module.consolidation', 'report.view_all', 'report.view_sent', 'report.edit_any');

ALTER TABLE users
    ADD COLUMN deleted_at DATETIME(6) NULL AFTER last_login_at,
    ADD COLUMN deleted_by BIGINT UNSIGNED NULL AFTER deleted_at,
    ADD KEY ix_users_deleted (deleted_at),
    ADD CONSTRAINT fk_users_deletedby FOREIGN KEY (deleted_by) REFERENCES users (id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_users_deleted CHECK ((deleted_at IS NULL AND deleted_by IS NULL)
                                        OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND is_active = 0));
