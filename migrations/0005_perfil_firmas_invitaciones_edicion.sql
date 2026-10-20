-- AJ System · Migración 0005 · firmas personales, invitaciones y edición transversal

ALTER TABLE users
    ADD COLUMN phone_number VARCHAR(15) NULL AFTER id_number,
    ADD CONSTRAINT ck_users_phone CHECK (phone_number IS NULL OR phone_number REGEXP '^[0-9]{8,15}$');

CREATE TABLE user_signatures (
    user_id     BIGINT UNSIGNED NOT NULL,
    mime        VARCHAR(32) NOT NULL,
    size_bytes  INT UNSIGNED NOT NULL,
    width       SMALLINT UNSIGNED NOT NULL,
    height      SMALLINT UNSIGNED NOT NULL,
    sha256      CHAR(64) NOT NULL,
    content     MEDIUMBLOB NOT NULL,
    updated_at  DATETIME(6) NOT NULL,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_user_signatures_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_user_signatures_size CHECK (size_bytes > 0 AND size_bytes <= 8388608)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_invitations (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id     BIGINT UNSIGNED NOT NULL,
    token_hash  CHAR(64) NOT NULL,
    created_by  BIGINT UNSIGNED NOT NULL,
    created_at  DATETIME(6) NOT NULL,
    expires_at  DATETIME(6) NOT NULL,
    used_at     DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_invitation_hash (token_hash),
    KEY ix_user_invitation_pending (user_id, used_at, expires_at),
    CONSTRAINT fk_user_invitation_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_user_invitation_creator FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_user_invitation_expiry CHECK (expires_at > created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Cualquier perfil puede consultar, reabrir con motivo y editar todos los informes.
-- La gestión de usuarios y la eliminación lógica siguen reservadas al responsable.
INSERT IGNORE INTO role_permissions (role_id, permission_id)
    SELECT r.id, p.id
      FROM roles r
      JOIN permissions p ON p.code IN ('module.consolidation', 'report.view_all', 'report.edit_any', 'report.reopen');

-- Las solicitudes de aprobación ya no forman parte del flujo para informes cerrados.
DELETE rp FROM role_permissions rp
  JOIN permissions p ON p.id = rp.permission_id
 WHERE p.code = 'report.request_reopen';
