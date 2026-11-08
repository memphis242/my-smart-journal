CREATE TABLE file_write_reservations (
 path TEXT PRIMARY KEY,
 owner_host TEXT NOT NULL,
 owner_pid INTEGER NOT NULL,
 owner_boot_id TEXT NOT NULL,
 owner_started_at TEXT NOT NULL
);
