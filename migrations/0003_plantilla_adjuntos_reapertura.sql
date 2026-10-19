-- AJ System · Migración 0003 · Plantilla oficial, adjuntos, solicitudes de reapertura e identificación
-- Compatible con MySQL 8.x y MariaDB 10.6+ (Hostinger).

-- ---------------------------------------------------------------------------
-- Usuarios: sin correo electrónico; número de identificación opcional y único
-- (se puede iniciar sesión con el usuario o con el número de identificación).
-- ---------------------------------------------------------------------------
ALTER TABLE users
    DROP COLUMN email,
    ADD COLUMN id_number VARCHAR(20) NULL AFTER full_name,
    ADD UNIQUE KEY uq_users_id_number (id_number),
    ADD CONSTRAINT ck_users_id_number CHECK (id_number IS NULL OR CHAR_LENGTH(id_number) >= 4);

-- ---------------------------------------------------------------------------
-- Informes: marca del equipo indexada junto al número de serie
-- ---------------------------------------------------------------------------
ALTER TABLE reports
    ADD COLUMN equipment_brand VARCHAR(100) NULL AFTER equipment_serial,
    ADD KEY ix_reports_brand_serial (equipment_brand, equipment_serial);

DROP TRIGGER trg_reports_state_guard;

DELIMITER $$
CREATE TRIGGER trg_reports_state_guard BEFORE UPDATE ON reports FOR EACH ROW
BEGIN
    IF NOT (
           NEW.status_code = OLD.status_code
        OR (OLD.status_code = 'EN_ELABORACION' AND NEW.status_code = 'CERRADO')
        OR (OLD.status_code = 'REABIERTO'      AND NEW.status_code = 'CERRADO')
        OR (OLD.status_code = 'CERRADO'        AND NEW.status_code = 'ENVIADO')
        OR (OLD.status_code = 'CERRADO'        AND NEW.status_code = 'REABIERTO')
        OR (OLD.status_code = 'ENVIADO'        AND NEW.status_code = 'REABIERTO')
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'transicion de estado no permitida';
    END IF;
    IF OLD.status_code IN ('CERRADO', 'ENVIADO') AND (
           NOT (NEW.data <=> OLD.data)
        OR NOT (NEW.title <=> OLD.title)
        OR NOT (NEW.equipment_serial <=> OLD.equipment_serial)
        OR NOT (NEW.equipment_brand <=> OLD.equipment_brand)
        OR NEW.revision <> OLD.revision
        OR NEW.owner_id <> OLD.owner_id
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'informe cerrado: contenido bloqueado';
    END IF;
    IF NEW.code <> OLD.code OR NEW.created_at <> OLD.created_at THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'campos inmutables del informe';
    END IF;
END$$
DELIMITER ;

-- ---------------------------------------------------------------------------
-- Adjuntos (fotos del equipo y firmas). Se guardan en la base de datos para que
-- persistan entre despliegues y entren en los respaldos. Nunca se borran: la
-- retirada de un adjunto es lógica (deleted_at) y solo con el informe abierto.
-- ---------------------------------------------------------------------------
CREATE TABLE report_attachments (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id      CHAR(32)      NOT NULL,
    report_id      BIGINT UNSIGNED NOT NULL,
    kind           ENUM('FOTO','FIRMA_ELABORA','FIRMA_RECIBE') NOT NULL,
    mime           VARCHAR(32)   NOT NULL,
    size_bytes     INT UNSIGNED  NOT NULL,
    width          SMALLINT UNSIGNED NOT NULL,
    height         SMALLINT UNSIGNED NOT NULL,
    sha256         CHAR(64)      NOT NULL,
    content        LONGBLOB      NOT NULL,
    thumb          MEDIUMBLOB    NULL,
    original_name  VARCHAR(160)  NOT NULL DEFAULT '',
    ocr_text       MEDIUMTEXT    NULL,
    created_by     BIGINT UNSIGNED NOT NULL,
    created_at     DATETIME(6)   NOT NULL,
    deleted_at     DATETIME(6)   NULL,
    deleted_by     BIGINT UNSIGNED NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_attachments_public (public_id),
    KEY ix_attachments_report (report_id, kind, deleted_at, created_at),
    CONSTRAINT fk_attachments_report  FOREIGN KEY (report_id)  REFERENCES reports (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attachments_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attachments_deleter FOREIGN KEY (deleted_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_attachments_size CHECK (size_bytes > 0 AND size_bytes <= 8388608),
    CONSTRAINT ck_attachments_del CHECK ((deleted_at IS NULL AND deleted_by IS NULL) OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DELIMITER $$
CREATE TRIGGER trg_attachments_insert_guard BEFORE INSERT ON report_attachments FOR EACH ROW
BEGIN
    IF (SELECT status_code FROM reports WHERE id = NEW.report_id) IN ('CERRADO', 'ENVIADO') THEN
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
    IF NOT (NEW.deleted_at <=> OLD.deleted_at)
       AND (SELECT status_code FROM reports WHERE id = NEW.report_id) IN ('CERRADO', 'ENVIADO') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'informe cerrado: adjuntos bloqueados';
    END IF;
END$$
DELIMITER ;

CREATE TRIGGER trg_attachments_no_delete BEFORE DELETE ON report_attachments FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'los adjuntos no se eliminan';

-- ---------------------------------------------------------------------------
-- Solicitudes de reapertura: el técnico pide permiso para volver a editar un
-- informe cerrado; el jefe o el responsable la aprueban (reabre) o la rechazan.
-- ---------------------------------------------------------------------------
CREATE TABLE reopen_requests (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_id        BIGINT UNSIGNED NOT NULL,
    requested_by     BIGINT UNSIGNED NOT NULL,
    reason           VARCHAR(1000) NOT NULL,
    status           ENUM('PENDIENTE','APROBADA','RECHAZADA') NOT NULL DEFAULT 'PENDIENTE',
    created_at       DATETIME(6)   NOT NULL,
    resolved_by      BIGINT UNSIGNED NULL,
    resolved_at      DATETIME(6)   NULL,
    resolution_note  VARCHAR(1000) NULL,
    -- Garantiza como máximo una solicitud pendiente por informe.
    pending_report_id BIGINT UNSIGNED AS (CASE WHEN status = 'PENDIENTE' THEN report_id END) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uq_reopen_pending (pending_report_id),
    KEY ix_reopen_status (status, created_at),
    KEY ix_reopen_report (report_id, id),
    CONSTRAINT fk_reopen_report    FOREIGN KEY (report_id)    REFERENCES reports (id) ON DELETE RESTRICT,
    CONSTRAINT fk_reopen_requester FOREIGN KEY (requested_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_reopen_resolver  FOREIGN KEY (resolved_by)  REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_reopen_reason CHECK (CHAR_LENGTH(TRIM(reason)) >= 10),
    CONSTRAINT ck_reopen_resolved CHECK ((status = 'PENDIENTE' AND resolved_at IS NULL AND resolved_by IS NULL)
                                      OR (status <> 'PENDIENTE' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TRIGGER trg_reopen_no_delete BEFORE DELETE ON reopen_requests FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'las solicitudes no se eliminan';

DELIMITER $$
CREATE TRIGGER trg_reopen_update_guard BEFORE UPDATE ON reopen_requests FOR EACH ROW
BEGIN
    IF OLD.status <> 'PENDIENTE' OR NEW.report_id <> OLD.report_id OR NEW.requested_by <> OLD.requested_by
       OR NEW.reason <> OLD.reason OR NEW.created_at <> OLD.created_at THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'solicitud resuelta o campos inmutables';
    END IF;
END$$
DELIMITER ;

ALTER TABLE report_events
    MODIFY action ENUM('CREAR','GUARDAR','CERRAR','ENVIAR','REABRIR','SOLICITAR_REAPERTURA','RECHAZAR_REAPERTURA') NOT NULL;

-- ---------------------------------------------------------------------------
-- Permisos nuevos
-- ---------------------------------------------------------------------------
INSERT INTO permissions (id, code, description) VALUES
    (14, 'report.view_all',       'Consultar y editar todos los informes, también los no enviados'),
    (15, 'report.request_reopen', 'Solicitar permiso para volver a editar un informe propio cerrado'),
    (16, 'report.import',         'Crear informes a partir de una matriz Excel o CSV');

INSERT INTO role_permissions (role_id, permission_id) VALUES
    (1, 14), (1, 15), (1, 16),
    (2, 14), (2, 15), (2, 16),
    (3, 15), (3, 16);
