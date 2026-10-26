-- AJ System · Migración 0007 · rol Director, firma de informes cerrados y paneles de actividad
--
-- Nada de lo existente se borra: solo cambia el nombre visible del rol RESPONSABLE, se añaden
-- permisos y un índice, y los disparadores de adjuntos admiten la firma de quien recibe en un
-- informe cerrado (el resto del contenido del informe sigue bloqueado).

UPDATE roles
   SET display_name = 'Director',
       description = 'Director: propietario funcional del sistema. Firma los informes cerrados.'
 WHERE code = 'RESPONSABLE';

INSERT IGNORE INTO permissions (id, code, description) VALUES
    (18, 'report.sign_closed',  'Firmar como quien recibe / aprueba los informes cerrados o enviados'),
    (19, 'dashboard.executive', 'Consultar el tablero ejecutivo de rendimiento e inventario'),
    (20, 'home.activity',       'Ver el panel de actividad propia en Inicio');

INSERT IGNORE INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id FROM roles r JOIN permissions p
        ON (r.code = 'RESPONSABLE' AND p.code IN ('report.sign_closed', 'dashboard.executive'))
        OR (r.code = 'TECNICO' AND p.code = 'home.activity');

-- Panel de actividad del técnico: informes propios no eliminados por fecha de creación.
ALTER TABLE reports ADD KEY ix_reports_live_owner_created (owner_id, deleted_at, created_at);

DROP TRIGGER IF EXISTS trg_attachments_insert_guard;
DROP TRIGGER IF EXISTS trg_attachments_update_guard;

DELIMITER $$
CREATE TRIGGER trg_attachments_insert_guard BEFORE INSERT ON report_attachments FOR EACH ROW
BEGIN
    IF NEW.kind <> 'FIRMA_RECIBE'
       AND (SELECT status_code FROM reports WHERE id = NEW.report_id) IN ('CERRADO', 'ENVIADO') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'informe cerrado: no admite adjuntos';
    END IF;
END$$

CREATE TRIGGER trg_attachments_update_guard BEFORE UPDATE ON report_attachments FOR EACH ROW
BEGIN
    IF NEW.public_id <> OLD.public_id OR NEW.report_id <> OLD.report_id OR NEW.kind <> OLD.kind
       OR NEW.sha256 <> OLD.sha256 OR NOT (NEW.content <=> OLD.content) OR NEW.created_at <> OLD.created_at
       OR NEW.created_by <> OLD.created_by THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'adjunto: contenido inmutable';
    END IF;
    IF OLD.deleted_at IS NOT NULL AND NOT (NEW.deleted_at <=> OLD.deleted_at) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'adjunto: retirada irreversible';
    END IF;
    IF OLD.ocr_text IS NOT NULL AND NOT (NEW.ocr_text <=> OLD.ocr_text) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'adjunto: texto reconocido inmutable';
    END IF;
    IF NOT (NEW.deleted_at <=> OLD.deleted_at) AND OLD.kind <> 'FIRMA_RECIBE'
       AND (SELECT status_code FROM reports WHERE id = NEW.report_id) IN ('CERRADO', 'ENVIADO') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'informe cerrado: adjuntos bloqueados';
    END IF;
END$$
DELIMITER ;
