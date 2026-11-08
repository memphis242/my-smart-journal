"""Local embedding jobs and cosine search over private journal passages."""

import hashlib
import logging
import re
import sqlite3
import struct
import threading
import uuid
from collections.abc import Mapping
from contextlib import nullcontext
from time import monotonic
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .config import Settings
from .diagnostics import safe_exception
from .storage import Store, timestamp

_logger = logging.getLogger(__name__)
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
FloatVector = NDArray[np.float32]


class EmbeddingFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Embedding operation failed")
        self.code = code


class EmbeddingService:
    """Create and query normalized passage vectors without exposing entry text in logs."""

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings.embedding
        self._model: Any = None
        self._model_error = False
        self._model_failures = 0
        self._model_retry_after = 0.0
        self._model_lock = threading.RLock()
        self._model_failure_code: str | None = None

    @property
    def fingerprint(self) -> str:
        material = (
            f"{self.settings.provider}\0{self.settings.model}\0{self.settings.device}"
            f"\0{self.settings.revision}\0{self.settings.chunk_tokens}"
            f"\0{self.settings.chunk_overlap}"
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def reconcile(self, db: sqlite3.Connection, entry_ids: set[str] | None = None) -> None:
        """Queue or refresh embedding work for every current revision that needs it."""
        if entry_ids is not None and not entry_ids:
            return
        if self.settings.provider == "none":
            selected_ids = tuple(sorted(entry_ids)) if entry_ids is not None else ()
            scope = (
                " AND entry_id IN (" + ",".join("?" for _ in selected_ids) + ")"
                if entry_ids is not None
                else ""
            )
            _ = db.execute(
                "UPDATE jobs SET status='superseded',"
                "last_error='Embeddings disabled by configuration',"
                "finished_at=?,lease_until=NULL,claim_token=NULL,claim_protocol=0 "
                "WHERE kind='embed' AND status IN ('queued','running') "
                "AND revision=(SELECT revision FROM entries WHERE id=jobs.entry_id)" + scope,
                (timestamp(), *selected_ids),
            )
            return
        if entry_ids is None:
            entries = db.execute("SELECT id,revision FROM entries ORDER BY id").fetchall()
        elif not entry_ids:
            return
        else:
            placeholders = ",".join("?" for _ in entry_ids)
            entries = db.execute(
                f"SELECT id,revision FROM entries WHERE id IN ({placeholders}) ORDER BY id",
                tuple(sorted(entry_ids)),
            ).fetchall()
        for entry in entries:
            entry_id = str(entry["id"])
            revision = int(entry["revision"])
            count = db.execute(
                "SELECT COUNT(*) FROM embeddings WHERE entry_id=? AND revision=? "
                "AND model_fingerprint=?",
                (entry_id, revision, self.fingerprint),
            ).fetchone()[0]
            if int(count) > 0:
                continue
            has_prior_vectors = (
                db.execute(
                    "SELECT 1 FROM embeddings WHERE entry_id=? AND revision=? LIMIT 1",
                    (entry_id, revision),
                ).fetchone()
                is not None
            )
            job = db.execute(
                "SELECT id,status FROM jobs WHERE entry_id=? AND revision=? AND kind='embed' "
                "AND target='' AND turn=0",
                (entry_id, revision),
            ).fetchone()
            if job is None:
                job_id = str(uuid.uuid4())
                _ = db.execute(
                    "INSERT INTO jobs (id,entry_id,kind,target,turn,not_before,created_at,revision)"
                    "VALUES (?,?,'embed','',0,strftime('%s','now'),?,?)",
                    (job_id, entry_id, timestamp(), revision),
                )
                embed_job_id = job_id
            elif job["status"] == "superseded" or (job["status"] == "done" and has_prior_vectors):
                _ = db.execute(
                    "UPDATE jobs SET status='queued',attempts=0,last_error=NULL,finished_at=NULL,"
                    "not_before=strftime('%s','now'),lease_until=NULL,claim_token=NULL,"
                    "claim_protocol=0 WHERE id=?",
                    (job["id"],),
                )
                embed_job_id = str(job["id"])
            else:
                embed_job_id = str(job["id"])
            prerequisites = db.execute(
                "SELECT id FROM jobs WHERE entry_id=? AND revision=? "
                "AND target IN ('cleanup','summarize') AND kind IN "
                "('cleanup','summarize','review','revise') AND status!='superseded' "
                "ORDER BY created_at,id",
                (entry_id, revision),
            ).fetchall()
            for prerequisite in prerequisites:
                dependency_id = str(prerequisite["id"])
                if dependency_id != embed_job_id:
                    _ = db.execute(
                        "INSERT OR IGNORE INTO job_dependencies(job_id,depends_on) VALUES (?,?)",
                        (embed_job_id, dependency_id),
                    )

    def process(self, job: Mapping[str, Any]) -> bool:
        """Embed a claimed entry and publish only while its lease and revision remain current."""
        if self.settings.provider == "none":
            return False
        with self.store.connect() as db:
            row = db.execute(
                "SELECT e.revision FROM entries e JOIN jobs j ON j.entry_id=e.id "
                "WHERE j.id=? AND j.status='running' AND j.claim_token=? AND e.revision=j.revision",
                (job["id"], job["claim_token"]),
            ).fetchone()
            if row is None:
                return False
            revision = int(row["revision"])
            artifacts = {
                str(item["kind"]): str(item["content"])
                for item in db.execute(
                    "SELECT kind,content FROM artifacts WHERE entry_id=? AND revision=? "
                    "AND superseded_by IS NULL",
                    (job["entry_id"], revision),
                )
            }
        cleaned = artifacts.get(
            "cleaned", artifacts.get("user_text", artifacts.get("raw_transcript", ""))
        )
        summary = artifacts.get("summary", "")
        passages = self._chunks(cleaned)
        if summary.strip():
            passages.append(summary.strip())
        if not passages:
            raise ValueError("No cleaned text is available for the embed job")
        vectors = self._encode(passages)
        with self.store.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            if not self.store._batch_owned(db, job):
                return False
            current = db.execute(
                "SELECT 1 FROM entries e JOIN jobs j ON j.entry_id=e.id "
                "WHERE j.id=? AND j.status='running' AND j.claim_token=? "
                "AND j.revision=e.revision",
                (job["id"], job["claim_token"]),
            ).fetchone()
            if current is None:
                return False
            _ = db.execute(
                "DELETE FROM embeddings WHERE entry_id=? AND revision=?",
                (job["entry_id"], revision),
            )
            for index, (passage, vector) in enumerate(zip(passages, vectors, strict=True)):
                _ = db.execute(
                    "INSERT INTO embeddings(entry_id,revision,chunk_index,passage,vector,"
                    "model_fingerprint,dimensions) VALUES (?,?,?,?,?,?,?)",
                    (
                        job["entry_id"],
                        revision,
                        index,
                        passage,
                        vector.astype(np.float32, copy=False).tobytes(),
                        self.fingerprint,
                        vector.size,
                    ),
                )
            updated = db.execute(
                "UPDATE jobs SET status='done',last_error=NULL,finished_at=?,lease_until=NULL,"
                "claim_token=NULL,claim_protocol=0 WHERE id=? AND status='running' "
                "AND claim_token=?",
                (timestamp(), job["id"], job["claim_token"]),
            )
            assert updated.rowcount == 1
        return True

    def search(
        self,
        query: str,
        allowed_ids: set[str] | None = None,
        *,
        db: sqlite3.Connection | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        """Return best matching chunks and a stable unavailable reason when needed."""
        if self.settings.provider == "none":
            return [], {
                "code": "EMBEDDINGS_DISABLED",
                "detail": (
                    "Semantic search is disabled; set embedding.provider to fake or "
                    "sentence-transformers."
                ),
            }
        try:
            query_vector = self._encode([query], query=True)[0]
        except Exception as exc:
            diagnostic = safe_exception(exc)
            _logger.error("Embedding query failed: %s", diagnostic)
            dependency_error: BaseException = exc
            while dependency_error.__cause__ is not None:
                dependency_error = dependency_error.__cause__
            missing_dependency = isinstance(dependency_error, ModuleNotFoundError)
            failure: BaseException | None = exc
            while failure is not None and not isinstance(failure, EmbeddingFailure):
                failure = failure.__cause__
            failure_code = (
                "EMBEDDING_DEPENDENCY_MISSING"
                if missing_dependency
                else failure.code
                if isinstance(failure, EmbeddingFailure)
                else self._model_failure_code or "EMBEDDING_ENCODING_FAILED"
            )
            if failure_code == "EMBEDDING_DEPENDENCY_MISSING":
                detail = (
                    "Install the backend embedding dependency with `pip install "
                    "my-smart-journal[semantic]`."
                )
            elif failure_code == "EMBEDDING_MODEL_LOAD_FAILED":
                detail = (
                    "The configured embedding model could not load; verify embedding.model, "
                    "embedding.revision, cache access, and model availability. See service logs."
                )
            else:
                detail = (
                    "The configured embedding model failed to encode the query; verify model "
                    "compatibility and dimensions. See service logs."
                )
            return [], {
                "code": failure_code,
                "detail": detail,
            }
        database = nullcontext(db) if db is not None else self.store.connect()
        with database as connection:
            rows = connection.execute(
                "SELECT x.entry_id,x.passage,x.vector,x.dimensions,e.revision "
                "FROM embeddings x JOIN entries e ON e.id=x.entry_id "
                "WHERE x.revision=e.revision AND x.model_fingerprint=? "
                "ORDER BY x.entry_id,x.chunk_index",
                (self.fingerprint,),
            ).fetchall()
            if allowed_ids is None:
                total = int(connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
                embedded = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT entry_id) FROM embeddings x JOIN entries e "
                        "ON e.id=x.entry_id WHERE x.revision=e.revision "
                        "AND x.model_fingerprint=?",
                        (self.fingerprint,),
                    ).fetchone()[0]
                )
            elif not allowed_ids:
                total = 0
                embedded = 0
            else:
                entry_ids = tuple(allowed_ids)
                placeholders = ",".join("?" for _ in entry_ids)
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM entries WHERE id IN ({placeholders})", entry_ids
                    ).fetchone()[0]
                )
                embedded = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT entry_id) FROM embeddings x JOIN entries e "
                        "ON e.id=x.entry_id WHERE x.revision=e.revision "
                        f"AND x.model_fingerprint=? AND x.entry_id IN ({placeholders})",
                        (self.fingerprint, *entry_ids),
                    ).fetchone()[0]
                )
        best: dict[str, tuple[float, str]] = {}
        for row in rows:
            entry_id = str(row["entry_id"])
            if allowed_ids is not None and entry_id not in allowed_ids:
                continue
            dimensions = int(row["dimensions"])
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            if dimensions != query_vector.size or vector.size != dimensions:
                continue
            score = float(np.dot(query_vector, vector))
            if score < self.settings.min_score:
                continue
            previous = best.get(entry_id)
            if previous is None or score > previous[0]:
                best[entry_id] = (score, str(row["passage"]))
        ranked = [
            {"entry_id": entry_id, "score": score, "passage": passage}
            for entry_id, (score, passage) in sorted(
                best.items(), key=lambda item: (-item[1][0], item[0])
            )[: self.settings.top_k]
        ]
        unavailable = None
        pending = max(0, total - embedded)
        if pending:
            unavailable = {
                "code": "EMBEDDINGS_PENDING",
                "detail": (
                    f"Semantic results may be incomplete while {pending} entries await embeddings."
                ),
            }
        return ranked, unavailable

    def _load_model(self) -> Any:
        with self._model_lock:
            if self._model is not None:
                return self._model
            now = monotonic()
            if self._model_error and now < self._model_retry_after:
                raise EmbeddingFailure(
                    self._model_failure_code or "EMBEDDING_MODEL_LOAD_FAILED"
                ) from None
            self._model_error = False
            try:
                if self.settings.provider == "fake":
                    self._model = "fake"
                else:
                    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found,unused-ignore]

                    self._model = SentenceTransformer(
                        self.settings.model,
                        device=self.settings.device,
                        revision=self.settings.revision,
                    )
            except Exception as exc:
                self._model_error = True
                root_cause: BaseException = exc
                while root_cause.__cause__ is not None:
                    root_cause = root_cause.__cause__
                self._model_failure_code = (
                    "EMBEDDING_DEPENDENCY_MISSING"
                    if isinstance(root_cause, ModuleNotFoundError)
                    else "EMBEDDING_MODEL_LOAD_FAILED"
                )
                self._model_failures += 1
                delay_s = min(30.0 * 2 ** min(self._model_failures - 1, 4), 300.0)
                self._model_retry_after = now + delay_s
                diagnostic = safe_exception(exc)
                _logger.error("Embedding model load failed: %s", diagnostic)
                raise EmbeddingFailure(self._model_failure_code) from exc
            return self._model

    def _encode(self, passages: list[str], *, query: bool = False) -> FloatVector:
        with self._model_lock:
            model = self._load_model()
            if model == "fake":
                vectors = np.stack([self._fake_vector(text) for text in passages])
            else:
                if query and "bge" in self.settings.model.casefold():
                    texts = [_QUERY_PREFIX + text for text in passages]
                    values = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
                elif query and callable(getattr(model, "encode_query", None)):
                    values = model.encode_query(
                        passages, normalize_embeddings=True, convert_to_numpy=True
                    )
                else:
                    values = model.encode(
                        passages, normalize_embeddings=True, convert_to_numpy=True
                    )
                vectors = np.asarray(values, dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(passages) or vectors.shape[1] < 1:
                raise RuntimeError("embedding model returned invalid vector dimensions")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(~np.isfinite(norms)) or np.any(norms == 0):
                raise RuntimeError("embedding model returned invalid vectors")
            return np.asarray(vectors / norms, dtype=np.float32)

    def _fake_vector(self, text: str) -> FloatVector:
        vector = np.zeros(384, dtype=np.float32)
        for token in re.findall(r"[a-z0-9]+", text.casefold()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            first, second = struct.unpack("<II", digest)
            index = first % vector.size
            vector[index] += 1.0 if second & 1 else -1.0
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            index = (
                int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "little") % vector.size
            )
            vector[index] = 1.0
            norm = 1.0
        if norm:
            vector /= norm
        return vector

    def _chunks(self, text: str) -> list[str]:
        if self.settings.provider != "fake":
            model = self._load_model()
            tokenizer = model.tokenizer
            token_ids = tokenizer.encode(text, add_special_tokens=False)
            if not token_ids:
                return []
            model_limit = int(model.max_seq_length)
            special_tokens = int(tokenizer.num_special_tokens_to_add(pair=False))
            safe_limit = max(1, model_limit - special_tokens)
            size = min(self.settings.chunk_tokens, safe_limit)
            overlap = min(self.settings.chunk_overlap, size - 1)
            step = size - overlap
            return [
                tokenizer.decode(token_ids[start : start + size], skip_special_tokens=True).strip()
                for start in range(0, len(token_ids), step)
            ]
        tokens = text.split()
        if not tokens:
            return []
        size = self.settings.chunk_tokens
        step = size - self.settings.chunk_overlap
        return [" ".join(tokens[start : start + size]) for start in range(0, len(tokens), step)]
