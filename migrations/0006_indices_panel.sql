-- AJ System · Migración 0006 · índices para listas y resumen del inicio

ALTER TABLE reports
    ADD KEY ix_reports_live_owner_status_updated (owner_id, deleted_at, status_code, updated_at),
    ADD KEY ix_reports_live_status_updated (deleted_at, status_code, updated_at),
    ADD KEY ix_reports_live_status_sent (deleted_at, status_code, last_sent_at);
