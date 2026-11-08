CREATE TEMP TABLE job_dependencies_v3 AS
SELECT job_id, depends_on FROM job_dependencies;
DROP TABLE job_dependencies;
CREATE TABLE jobs_v3 (
 id TEXT PRIMARY KEY,
 entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('transcribe','cleanup','review','summarize','extract')),
 status TEXT NOT NULL DEFAULT 'queued'
   CHECK(status IN ('queued','running','done','failed','superseded')),
 attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,
 not_before REAL NOT NULL,
 created_at TEXT NOT NULL,
 finished_at TEXT,
 revision INTEGER NOT NULL,
 lease_until REAL,
 claim_token TEXT,
 UNIQUE(entry_id,revision,kind)
);
INSERT INTO jobs_v3
SELECT id, entry_id, kind,
 CASE WHEN status='failed' AND revision <
   (SELECT revision FROM entries WHERE entries.id=jobs.entry_id)
   THEN 'superseded' ELSE status END,
 attempts, last_error, not_before, created_at, finished_at, revision, lease_until, claim_token
FROM jobs;
DROP INDEX jobs_ready;
DROP TABLE jobs;
ALTER TABLE jobs_v3 RENAME TO jobs;
CREATE INDEX jobs_ready ON jobs(status,not_before);
CREATE TABLE job_dependencies (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 depends_on TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 PRIMARY KEY(job_id,depends_on), CHECK(job_id != depends_on)
);
INSERT INTO job_dependencies SELECT job_id,depends_on FROM job_dependencies_v3;
DROP TABLE job_dependencies_v3;
