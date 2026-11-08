CREATE TABLE compilations (
 id TEXT PRIMARY KEY,
 created_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
 entry_ids TEXT NOT NULL,
 source_content TEXT NOT NULL,
 purpose TEXT NOT NULL,
 period TEXT NOT NULL,
 result TEXT,
 last_error TEXT,
 model TEXT,
 prompt_sha256 TEXT
);

CREATE TABLE batch_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 batch_id TEXT,
 started_at TEXT,
 trigger TEXT CHECK(trigger IN ('manual','schedule')),
 lease_until REAL,
 last_finished_at TEXT,
 last_result TEXT,
 worker_last_seen_at TEXT
);
INSERT INTO batch_state (id) VALUES (1);

CREATE TEMP TABLE job_dependencies_v6 AS
SELECT job_id,depends_on FROM job_dependencies;
DROP TABLE job_dependencies;
CREATE TABLE jobs_v6 (
 id TEXT PRIMARY KEY,
 entry_id TEXT REFERENCES entries(id) ON DELETE CASCADE,
 compilation_id TEXT REFERENCES compilations(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('transcribe','cleanup','review','summarize','extract','revise','compile','embed')),
 target TEXT NOT NULL DEFAULT '',
 turn INTEGER NOT NULL DEFAULT 0 CHECK(turn BETWEEN 0 AND 3),
 input_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
 review_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
 result_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
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
 claim_protocol INTEGER NOT NULL DEFAULT 0,
 CHECK((status='running' AND claim_protocol=4) OR (status!='running' AND claim_protocol=0)),
 CHECK((entry_id IS NOT NULL AND compilation_id IS NULL AND kind!='compile') OR
       (entry_id IS NULL AND compilation_id IS NOT NULL AND kind='compile')),
 CHECK((kind='transcribe' AND target='' AND turn=0) OR
       (kind IN ('cleanup','summarize','extract') AND target=kind AND turn=0) OR
       (kind='review' AND target IN ('cleanup','summarize','extract') AND turn BETWEEN 0 AND 3) OR
       (kind='revise' AND target IN ('cleanup','summarize','extract') AND turn BETWEEN 1 AND 2) OR
       (kind IN ('compile','embed') AND target='' AND turn=0)),
 UNIQUE(entry_id,revision,kind,target,turn),
 UNIQUE(compilation_id)
);
INSERT INTO jobs_v6
 (id,entry_id,compilation_id,kind,target,turn,input_artifact_id,review_artifact_id,
  result_artifact_id,status,attempts,last_error,not_before,created_at,finished_at,revision,
  lease_until,claim_token,claim_protocol)
SELECT id,entry_id,NULL,kind,target,turn,input_artifact_id,review_artifact_id,result_artifact_id,
 status,attempts,last_error,not_before,created_at,finished_at,revision,lease_until,claim_token,
 claim_protocol FROM jobs;
DROP INDEX jobs_ready;
DROP TABLE jobs;
ALTER TABLE jobs_v6 RENAME TO jobs;
CREATE INDEX jobs_ready ON jobs(status,not_before);
CREATE INDEX jobs_compilation ON jobs(compilation_id);
CREATE TABLE job_dependencies (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 depends_on TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 PRIMARY KEY(job_id,depends_on), CHECK(job_id != depends_on)
);
INSERT INTO job_dependencies SELECT job_id,depends_on FROM job_dependencies_v6;
DROP TABLE job_dependencies_v6;
