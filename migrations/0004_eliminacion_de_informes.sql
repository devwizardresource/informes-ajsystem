-- AJ System · Migración 0004 · Eliminación de informes por el responsable administrativo (admin)
-- La eliminación es lógica: el informe desaparece de todas las vistas y consultas de la aplicación,
-- pero su historial y la auditoría encadenada se conservan (borrar filas rompería la cadena HMAC).
-- El responsable puede consultar los informes eliminados y restaurarlos.

ALTER TABLE reports
    ADD COLUMN deleted_at      DATETIME(6)   NULL AFTER reopen_count,
    ADD COLUMN deleted_by      BIGINT UNSIGNED NULL AFTER deleted_at,
    ADD COLUMN deletion_reason VARCHAR(1000) NULL AFTER deleted_by,
    ADD KEY ix_reports_deleted (deleted_at),
    ADD CONSTRAINT fk_reports_deletedby FOREIGN KEY (deleted_by) REFERENCES users (id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_reports_deleted CHECK ((deleted_at IS NULL AND deleted_by IS NULL AND deletion_reason IS NULL)
                                          OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL
                                              AND CHAR_LENGTH(TRIM(deletion_reason)) >= 10));

INSERT INTO permissions (id, code, description) VALUES
    (17, 'report.delete', 'Eliminar y restaurar informes (solo el responsable administrativo)');
INSERT INTO role_permissions (role_id, permission_id) VALUES (1, 17);
