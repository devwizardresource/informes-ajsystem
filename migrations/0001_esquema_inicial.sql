-- AJ System · Migración 0001 · Esquema inicial
-- Motor: MySQL 8.4 (InnoDB, utf8mb4). Todas las fechas se guardan en UTC.
-- Esta migración la ejecuta la cuenta propietaria (DDL), nunca la cuenta de la aplicación.

-- ---------------------------------------------------------------------------
-- Roles, permisos y usuarios
-- ---------------------------------------------------------------------------
CREATE TABLE roles (
    id            TINYINT UNSIGNED NOT NULL,
    code          VARCHAR(32)  NOT NULL,
    display_name  VARCHAR(80)  NOT NULL,
    description   VARCHAR(255) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uq_roles_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE permissions (
    id           SMALLINT UNSIGNED NOT NULL,
    code         VARCHAR(48)  NOT NULL,
    description  VARCHAR(255) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_permissions_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE role_permissions (
    role_id        TINYINT UNSIGNED  NOT NULL,
    permission_id  SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_rp_perm FOREIGN KEY (permission_id) REFERENCES permissions (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
    id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    username              VARCHAR(64)  NOT NULL,
    full_name             VARCHAR(120) NOT NULL,
    email                 VARCHAR(190) NULL,
    password_hash         VARCHAR(255) NOT NULL,
    role_id               TINYINT UNSIGNED NOT NULL,
    is_active             TINYINT(1)   NOT NULL DEFAULT 1,
    must_change_password  TINYINT(1)   NOT NULL DEFAULT 1,
    created_at            DATETIME(6)  NOT NULL,
    updated_at            DATETIME(6)  NOT NULL,
    password_changed_at   DATETIME(6)  NULL,
    last_login_at         DATETIME(6)  NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username),
    KEY ix_users_role (role_id),
    CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE RESTRICT,
    CONSTRAINT ck_users_username CHECK (CHAR_LENGTH(username) >= 3),
    CONSTRAINT ck_users_active CHECK (is_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Sesiones del lado del servidor. La cookie contiene un token aleatorio; aquí solo
-- se guarda su hash SHA-256. public_id es un identificador NO secreto para auditoría.
CREATE TABLE sessions (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    public_id            CHAR(32)     NOT NULL,
    token_hash           CHAR(64)     NOT NULL,
    user_id              BIGINT UNSIGNED NOT NULL,
    csrf_token           CHAR(64)     NOT NULL,
    created_at           DATETIME(6)  NOT NULL,
    last_seen_at         DATETIME(6)  NOT NULL,
    absolute_expires_at  DATETIME(6)  NOT NULL,
    ended_at             DATETIME(6)  NULL,
    end_reason           ENUM('LOGOUT','EXPIRADA','REVOCADA') NULL,
    ip                   VARCHAR(45)  NOT NULL,
    user_agent           VARCHAR(512) NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uq_sessions_public (public_id),
    UNIQUE KEY uq_sessions_token (token_hash),
    KEY ix_sessions_user (user_id, ended_at),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_sessions_end CHECK ((ended_at IS NULL AND end_reason IS NULL) OR (ended_at IS NOT NULL AND end_reason IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Informes
-- ---------------------------------------------------------------------------
CREATE TABLE report_statuses (
    code         VARCHAR(20) NOT NULL,
    label        VARCHAR(60) NOT NULL,
    is_editable  TINYINT(1)  NOT NULL,
    sort_order   TINYINT UNSIGNED NOT NULL,
    PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Secuencia anual para el identificador visible del informe (p. ej. AJ-2026-000001).
CREATE TABLE report_counters (
    year        SMALLINT UNSIGNED NOT NULL,
    current_value  INT UNSIGNED NOT NULL,
    PRIMARY KEY (year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE reports (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code              VARCHAR(32)  NOT NULL,
    owner_id          BIGINT UNSIGNED NOT NULL,
    status_code       VARCHAR(20)  NOT NULL,
    template_code     VARCHAR(40)  NOT NULL,
    title             VARCHAR(200) NOT NULL DEFAULT '',
    equipment_serial  VARCHAR(100) NULL,
    data              JSON         NOT NULL,
    revision          INT UNSIGNED NOT NULL DEFAULT 1,
    created_at        DATETIME(6)  NOT NULL,
    updated_at        DATETIME(6)  NOT NULL,
    updated_by        BIGINT UNSIGNED NOT NULL,
    closed_at         DATETIME(6)  NULL,
    closed_by         BIGINT UNSIGNED NULL,
    first_sent_at     DATETIME(6)  NULL,
    last_sent_at      DATETIME(6)  NULL,
    last_sent_by      BIGINT UNSIGNED NULL,
    reopen_count      INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uq_reports_code (code),
    KEY ix_reports_owner_status (owner_id, status_code, updated_at),
    KEY ix_reports_status_updated (status_code, updated_at),
    KEY ix_reports_serial (equipment_serial),
    KEY ix_reports_sent (first_sent_at),
    KEY ix_reports_created (created_at),
    CONSTRAINT fk_reports_owner   FOREIGN KEY (owner_id)     REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_reports_status  FOREIGN KEY (status_code)  REFERENCES report_statuses (code) ON DELETE RESTRICT,
    CONSTRAINT fk_reports_updby   FOREIGN KEY (updated_by)   REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_reports_closeby FOREIGN KEY (closed_by)    REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_reports_sentby  FOREIGN KEY (last_sent_by) REFERENCES users (id) ON DELETE RESTRICT,
    -- Un informe cerrado o enviado debe tener cierre registrado; uno enviado, envío registrado.
    CONSTRAINT ck_reports_closed CHECK (status_code NOT IN ('CERRADO','ENVIADO') OR (closed_at IS NOT NULL AND closed_by IS NOT NULL)),
    CONSTRAINT ck_reports_sent   CHECK (status_code <> 'ENVIADO' OR (last_sent_at IS NOT NULL AND last_sent_by IS NOT NULL AND first_sent_at IS NOT NULL)),
    CONSTRAINT ck_reports_rev    CHECK (revision >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Instantánea completa de cada guardado (historial de versiones). Solo inserción.
CREATE TABLE report_versions (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_id       BIGINT UNSIGNED NOT NULL,
    revision        INT UNSIGNED NOT NULL,
    title           VARCHAR(200) NOT NULL,
    equipment_serial VARCHAR(100) NULL,
    data            JSON         NOT NULL,
    content_sha256  CHAR(64)     NOT NULL,
    saved_by        BIGINT UNSIGNED NOT NULL,
    saved_at        DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_versions_report_rev (report_id, revision),
    CONSTRAINT fk_versions_report FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE RESTRICT,
    CONSTRAINT fk_versions_user   FOREIGN KEY (saved_by)  REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Historial de operaciones del ciclo de vida (crear, guardar, cerrar, enviar, reabrir). Solo inserción.
CREATE TABLE report_events (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    report_id        BIGINT UNSIGNED NOT NULL,
    action           ENUM('CREAR','GUARDAR','CERRAR','ENVIAR','REABRIR') NOT NULL,
    from_status      VARCHAR(20)  NULL,
    to_status        VARCHAR(20)  NOT NULL,
    revision         INT UNSIGNED NOT NULL,
    user_id          BIGINT UNSIGNED NOT NULL,
    reason           VARCHAR(1000) NULL,
    idempotency_key  CHAR(32)     NULL,
    created_at       DATETIME(6)  NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_events_idem (report_id, action, idempotency_key),
    KEY ix_events_report (report_id, id),
    CONSTRAINT fk_events_report FOREIGN KEY (report_id)   REFERENCES reports (id) ON DELETE RESTRICT,
    CONSTRAINT fk_events_user   FOREIGN KEY (user_id)     REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_events_from   FOREIGN KEY (from_status) REFERENCES report_statuses (code),
    CONSTRAINT fk_events_to     FOREIGN KEY (to_status)   REFERENCES report_statuses (code),
    CONSTRAINT ck_events_reason CHECK (action <> 'REABRIR' OR CHAR_LENGTH(TRIM(reason)) >= 10)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Auditoría de accesos y eventos (solo inserción, encadenada por HMAC)
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    seq                BIGINT UNSIGNED NOT NULL,
    event_uuid         CHAR(32)     NOT NULL,
    occurred_at        DATETIME(6)  NOT NULL,
    event_type         VARCHAR(48)  NOT NULL,
    outcome            ENUM('EXITO','FALLO','DENEGADO','ERROR') NOT NULL,
    user_id            BIGINT UNSIGNED NULL,
    username           VARCHAR(64)  NULL,
    role_code          VARCHAR(32)  NULL,
    session_public_id  CHAR(32)     NULL,
    ip                 VARCHAR(45)  NULL,
    os_reported        VARCHAR(64)  NULL,
    user_agent         VARCHAR(512) NULL,
    object_type        VARCHAR(32)  NULL,
    object_id          VARCHAR(64)  NULL,
    detail             JSON         NOT NULL,
    prev_hash          CHAR(64)     NOT NULL,
    row_hash           CHAR(64)     NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_audit_seq (seq),
    UNIQUE KEY uq_audit_uuid (event_uuid),
    KEY ix_audit_time (occurred_at),
    KEY ix_audit_type_user_time (event_type, username, occurred_at),
    KEY ix_audit_type_ip_time (event_type, ip, occurred_at),
    KEY ix_audit_user_time (user_id, occurred_at),
    KEY ix_audit_object (object_type, object_id),
    CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_audit_type CHECK (CHAR_LENGTH(event_type) >= 3),
    CONSTRAINT ck_audit_hash CHECK (CHAR_LENGTH(row_hash) = 64 AND CHAR_LENGTH(prev_hash) = 64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Cabeza de la cadena: una sola fila bloqueada con FOR UPDATE para serializar las inserciones.
CREATE TABLE audit_chain_head (
    id        TINYINT UNSIGNED NOT NULL,
    last_seq  BIGINT UNSIGNED NOT NULL,
    last_hash CHAR(64) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_head_single CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE system_settings (
    setting_key    VARCHAR(64)  NOT NULL,
    setting_value  VARCHAR(1000) NOT NULL,
    updated_at     DATETIME(6)  NOT NULL,
    PRIMARY KEY (setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Disparadores de integridad (defensa en profundidad, además de los GRANT)
-- ---------------------------------------------------------------------------
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_log es de solo insercion';

CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_log es de solo insercion';

CREATE TRIGGER trg_versions_no_update BEFORE UPDATE ON report_versions FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'report_versions es de solo insercion';

CREATE TRIGGER trg_versions_no_delete BEFORE DELETE ON report_versions FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'report_versions es de solo insercion';

CREATE TRIGGER trg_events_no_update BEFORE UPDATE ON report_events FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'report_events es de solo insercion';

CREATE TRIGGER trg_events_no_delete BEFORE DELETE ON report_events FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'report_events es de solo insercion';

CREATE TRIGGER trg_reports_no_delete BEFORE DELETE ON reports FOR EACH ROW
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'los informes no se eliminan';

-- Máquina de estados y bloqueo de contenido de informes cerrados, aplicados también en la base de datos.
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
