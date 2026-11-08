CREATE TABLE sessions (
 session_hash TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 expires_at REAL NOT NULL
);
CREATE INDEX sessions_expiry ON sessions(expires_at);
