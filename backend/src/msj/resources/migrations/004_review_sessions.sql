ALTER TABLE artifacts ADD COLUMN target TEXT;
ALTER TABLE artifacts ADD COLUMN turn INTEGER NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN reviewed_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE;
UPDATE artifacts SET target='cleanup' WHERE kind='review';
UPDATE artifacts AS review_artifact SET reviewed_artifact_id=(
 SELECT candidate.id FROM artifacts AS candidate
 WHERE candidate.entry_id=review_artifact.entry_id
   AND candidate.revision=review_artifact.revision AND candidate.kind='cleaned'
 ORDER BY candidate.created_at DESC,candidate.id DESC LIMIT 1
) WHERE review_artifact.kind='review';
UPDATE artifacts SET target=CASE kind WHEN 'cleaned' THEN 'cleanup'
 WHEN 'summary' THEN 'summarize' WHEN 'extraction' THEN 'extract' END
WHERE kind IN ('cleaned','summary','extraction');

CREATE TEMP TABLE job_dependencies_v4 AS
SELECT job_id,depends_on FROM job_dependencies;
DROP TABLE job_dependencies;
CREATE TABLE jobs_v4 (
 id TEXT PRIMARY KEY,
 entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 kind TEXT NOT NULL CHECK(kind IN ('transcribe','cleanup','review','summarize','extract','revise')),
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
 UNIQUE(entry_id,revision,kind,target,turn),
 CHECK((kind='transcribe' AND target='' AND turn=0) OR
       (kind IN ('cleanup','summarize','extract') AND target=kind AND turn=0) OR
       (kind='review' AND target IN ('cleanup','summarize','extract') AND turn BETWEEN 0 AND 3) OR
       (kind='revise' AND target IN ('cleanup','summarize','extract') AND turn BETWEEN 1 AND 2))
);
INSERT INTO jobs_v4
 (id,entry_id,kind,target,turn,status,attempts,last_error,not_before,created_at,finished_at,
  revision,lease_until,claim_token)
SELECT id,entry_id,kind,
 CASE WHEN kind='review' THEN 'cleanup'
      WHEN kind IN ('cleanup','summarize','extract') THEN kind ELSE '' END,
 0,
 CASE WHEN kind='review' AND status IN ('queued','running','failed') THEN 'superseded'
      WHEN status='running' THEN 'queued'
      ELSE status END,
 attempts,last_error,not_before,created_at,finished_at,revision,
 CASE WHEN status='running' THEN NULL ELSE lease_until END,
 CASE WHEN status='running' THEN NULL ELSE claim_token END
FROM jobs;
DROP INDEX jobs_ready;
DROP TABLE jobs;
ALTER TABLE jobs_v4 RENAME TO jobs;
CREATE INDEX jobs_ready ON jobs(status,not_before);
CREATE TABLE job_dependencies (
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 depends_on TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 PRIMARY KEY(job_id,depends_on), CHECK(job_id != depends_on)
);
INSERT INTO job_dependencies SELECT job_id,depends_on FROM job_dependencies_v4;
DROP TABLE job_dependencies_v4;

CREATE TABLE review_sessions (
 entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL,
 target TEXT NOT NULL CHECK(target IN ('cleanup','summarize','extract')),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 max_turns INTEGER NOT NULL CHECK(max_turns BETWEEN 1 AND 3),
 status TEXT NOT NULL CHECK(status IN ('pending','accepted','needs_attention','disabled')),
 turns_used INTEGER NOT NULL DEFAULT 0 CHECK(turns_used BETWEEN 0 AND 3),
 remaining_claims TEXT NOT NULL DEFAULT '[]',
 producer_route TEXT,
 source_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
 candidate_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
 final_artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
 PRIMARY KEY(entry_id,revision,target)
);
