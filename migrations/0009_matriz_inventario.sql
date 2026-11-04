-- AJ System · Matriz de inventario: equipos reales que hay que auditar.
-- Cada carga es un lote de solo inserción: la vigente es la última y las anteriores quedan como
-- historial. Los datos los carga el Director desde el tablero; no viven en el repositorio.

CREATE TABLE inventory_imports (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    file_name    VARCHAR(190)    NOT NULL,
    sha256       CHAR(64)        NOT NULL,
    item_count   INT UNSIGNED    NOT NULL,
    imported_by  BIGINT UNSIGNED NOT NULL,
    imported_at  DATETIME(6)     NOT NULL,
    CONSTRAINT fk_inventory_imports_user FOREIGN KEY (imported_by) REFERENCES users (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE inventory_items (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    import_id        BIGINT UNSIGNED NOT NULL,
    item_no          INT UNSIGNED    NOT NULL,
    serial           VARCHAR(100)    NULL,
    quantity         INT UNSIGNED    NULL,
    characteristics  VARCHAR(2000)   NULL,
    category         VARCHAR(120)    NULL,
    UNIQUE KEY uq_inventory_items_no (import_id, item_no),
    CONSTRAINT fk_inventory_items_import FOREIGN KEY (import_id) REFERENCES inventory_imports (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
