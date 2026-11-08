CREATE TABLE embeddings (
 entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL,
 chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
 passage TEXT NOT NULL,
 vector BLOB NOT NULL,
 model_fingerprint TEXT NOT NULL,
 dimensions INTEGER NOT NULL CHECK(dimensions > 0),
 PRIMARY KEY(entry_id,revision,chunk_index,model_fingerprint)
);
CREATE INDEX embeddings_current ON embeddings(entry_id,revision,model_fingerprint);
