ALTER TABLE batch_state ADD COLUMN owner_host TEXT;
ALTER TABLE batch_state ADD COLUMN owner_pid INTEGER;
ALTER TABLE batch_state ADD COLUMN owner_boot_id TEXT;
ALTER TABLE batch_state ADD COLUMN owner_started_at TEXT;
ALTER TABLE batch_state ADD COLUMN request_pending INTEGER NOT NULL DEFAULT 0;
ALTER TABLE batch_state ADD COLUMN request_created_at TEXT;
ALTER TABLE batch_state ADD COLUMN last_error TEXT;
ALTER TABLE batch_state ADD COLUMN worker_next_run_at TEXT;

ALTER TABLE jobs ADD COLUMN batch_id TEXT;

UPDATE auth SET token_hash='reissue-required'
WHERE token_hash NOT LIKE 'hmac-sha256$%';

DELETE FROM sessions WHERE EXISTS (SELECT 1 FROM auth WHERE token_hash='reissue-required');
