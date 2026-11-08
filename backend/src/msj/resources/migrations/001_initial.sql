CREATE TABLE entries (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, entry_date TEXT NOT NULL,
 title TEXT, tags TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL CHECK(source IN ('text','audio')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0)
);
CREATE TABLE artifacts (
 id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('raw_audio','raw_transcript','user_text','cleaned','summary','extraction','review')),
 content TEXT NOT NULL, model TEXT, prompt_sha256 TEXT, created_at TEXT NOT NULL,
 superseded_by TEXT REFERENCES artifacts(id) DEFERRABLE INITIALLY DEFERRED,
 revision INTEGER NOT NULL, needs_attention INTEGER NOT NULL DEFAULT 0 CHECK(needs_attention IN (0,1))
);
CREATE INDEX artifacts_entry ON artifacts(entry_id,kind,revision);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('transcribe','cleanup','review','summarize','extract')),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed')),
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, not_before REAL NOT NULL,
 created_at TEXT NOT NULL, finished_at TEXT, revision INTEGER NOT NULL,
 lease_until REAL, claim_token TEXT, UNIQUE(entry_id,revision,kind)
);
CREATE TABLE job_dependencies (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 depends_on TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 PRIMARY KEY(job_id,depends_on), CHECK(job_id != depends_on)
);
CREATE INDEX jobs_ready ON jobs(status,not_before);
CREATE VIRTUAL TABLE search_index USING fts5(entry_id UNINDEXED, text, summary, title, tags);
CREATE TABLE auth (id INTEGER PRIMARY KEY CHECK(id=1), token_hash TEXT NOT NULL);
