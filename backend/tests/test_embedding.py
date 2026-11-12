"""Behavioral checks for local embedding and semantic search."""

import os
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from msj.config import EmbeddingSettings, Settings
from msj.embedding import EmbeddingService
from msj.storage import Store


def test_fake_embeddings_publish_chunks_and_rank_related_entry(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="fake", chunk_tokens=4, chunk_overlap=1),
    )
    store = Store(settings)
    store.migrate()
    first = store.create_entry(text="I used python to build a secure journal search.")
    second = store.create_entry(text="I baked bread and planted flowers in the garden.")
    service = EmbeddingService(store, settings)
    with store.connect() as db:
        rows = db.execute("SELECT id,entry_id,revision FROM jobs WHERE kind='embed'").fetchall()
        for index, job in enumerate(rows):
            token = f"claim-{index}"
            _ = db.execute(
                "UPDATE jobs SET status='running',claim_token=?,claim_protocol=4 WHERE id=?",
                (token, job["id"]),
            )
            db.commit()
            assert service.process(
                {
                    "id": job["id"],
                    "entry_id": job["entry_id"],
                    "revision": job["revision"],
                    "claim_token": token,
                }
            )
        chunks = db.execute(
            "SELECT passage,dimensions,length(vector) AS bytes FROM embeddings "
            "WHERE entry_id=? ORDER BY chunk_index",
            (first["id"],),
        ).fetchall()
    assert len(chunks) >= 2
    assert all(row["dimensions"] == 384 and row["bytes"] == 384 * 4 for row in chunks)
    results, unavailable = service.search("python secure search")
    assert results[0]["entry_id"] == first["id"]
    assert results[0]["passage"]
    assert unavailable is None
    assert second["id"] not in {item["entry_id"] for item in results}


def test_fake_embedder_is_deterministic_and_none_is_reported(tmp_path: Path) -> None:
    fake_settings = Settings(
        data_dir=tmp_path / "fake", embedding=EmbeddingSettings(provider="fake")
    )
    service = EmbeddingService(Store(fake_settings), fake_settings)
    assert (service._encode(["shared meaning"])[0] == service._encode(["shared meaning"])[0]).all()

    disabled_settings = Settings(
        data_dir=tmp_path / "none", embedding=EmbeddingSettings(provider="none")
    )
    disabled = EmbeddingService(Store(disabled_settings), disabled_settings)
    results, unavailable = disabled.search("anything")
    assert results == []
    assert unavailable is not None and unavailable["code"] == "EMBEDDINGS_DISABLED"


def test_missing_model_has_stable_unavailable_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake"))
    service = EmbeddingService(Store(settings), settings)
    service._model_error = True
    service._model_retry_after = float("inf")
    service._model_failure_code = "EMBEDDING_MODEL_LOAD_FAILED"
    results, unavailable = service.search("secret journal contents")
    assert results == []
    assert unavailable is not None
    assert unavailable["code"] == "EMBEDDING_MODEL_LOAD_FAILED"
    assert "secret journal contents" not in caplog.text


def test_model_load_recovers_after_bounded_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="sentence-transformers", model="test-model"),
    )
    store = Store(settings)
    store.migrate()
    service = EmbeddingService(store, settings)
    now = [100.0]
    attempts: list[str] = []

    class LoadedModel:
        max_seq_length = 8

        def encode(self, texts: list[str], **_kwargs: Any) -> np.ndarray[Any, Any]:
            return np.ones((len(texts), 4), dtype=np.float32)

    def load_model(
        _model_name: str, *, device: str, revision: str, local_files_only: bool
    ) -> LoadedModel:
        assert revision == settings.embedding.revision
        assert local_files_only, "serving must never fetch the model from the network"
        attempts.append(device)
        if len(attempts) == 1:
            raise RuntimeError("private provider value")
        return LoadedModel()

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = load_model
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setattr("msj.embedding.monotonic", lambda: now[0])

    _results, unavailable = service.search("secret query")
    assert unavailable is not None and unavailable["code"] == "EMBEDDING_MODEL_LOAD_FAILED"
    assert attempts == ["cpu"]
    _results, still_unavailable = service.search("another query")
    assert still_unavailable is not None
    assert attempts == ["cpu"]

    now[0] = service._model_retry_after + 1
    results, recovered = service.search("private entry text")
    assert results == []
    assert recovered is None
    assert attempts == ["cpu", "cpu"]


def test_missing_embedding_dependency_keeps_its_code_during_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="sentence-transformers", model="test-model"),
    )
    service = EmbeddingService(Store(settings), settings)

    def missing_dependency(*_args: Any, **_kwargs: Any) -> Any:
        raise ModuleNotFoundError("private module detail")

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = missing_dependency
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    first = service.search("secret query")[1]
    second = service.search("secret query")[1]

    assert first is not None and first["code"] == "EMBEDDING_DEPENDENCY_MISSING"
    assert second is not None and second["code"] == "EMBEDDING_DEPENDENCY_MISSING"
    assert "my-smart-journal[semantic]" in first["detail"]
    assert "private module detail" not in first["detail"]


def test_pending_detail_counts_only_filtered_entries(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake"))
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="A note awaiting semantic indexing.")
    other = store.create_entry(text="A second note awaiting semantic indexing.")
    service = EmbeddingService(store, settings)
    with store.connect() as db:
        job = db.execute(
            "SELECT id,entry_id,revision FROM jobs WHERE kind='embed' AND entry_id=?",
            (entry["id"],),
        ).fetchone()
        assert job is not None
        token = "filtered-pending-claim"
        _ = db.execute(
            "UPDATE jobs SET status='running',claim_token=?,claim_protocol=4 WHERE id=?",
            (token, job["id"]),
        )
        db.commit()
    assert service.process(
        {
            "id": job["id"],
            "entry_id": job["entry_id"],
            "revision": job["revision"],
            "claim_token": token,
        }
    )

    entries, unavailable = service.search("note", allowed_ids={entry["id"]})
    assert entries and entries[0]["entry_id"] == entry["id"]
    assert unavailable is None
    entries, unavailable = service.search("note", allowed_ids={other["id"]})
    assert entries == []
    assert unavailable is not None
    assert unavailable["code"] == "EMBEDDINGS_PENDING"
    assert "1 entries" in unavailable["detail"]
    assert service.search("note", allowed_ids=set())[1] is None


def test_stale_claim_cannot_publish_after_entry_revision_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake"))
    store = Store(settings)
    store.migrate()
    entry = store.create_entry(text="The original entry text.")
    service = EmbeddingService(store, settings)
    with store.connect() as db:
        job = db.execute(
            "SELECT id,entry_id,revision FROM jobs WHERE kind='embed' LIMIT 1"
        ).fetchone()
        assert job is not None
        token = "stale-revision-claim"
        _ = db.execute(
            "UPDATE jobs SET status='running',claim_token=?,claim_protocol=4 WHERE id=?",
            (token, job["id"]),
        )
        db.commit()

    encode = service._encode

    def revise_before_publish(passages: list[str], *, query: bool = False) -> Any:
        _ = store.patch_entry(entry["id"], {"text": "The corrected entry text."})
        return encode(passages, query=query)

    monkeypatch.setattr(service, "_encode", revise_before_publish)
    assert not service.process(
        {
            "id": job["id"],
            "entry_id": job["entry_id"],
            "revision": job["revision"],
            "claim_token": token,
        }
    )
    with store.connect() as db:
        vectors = db.execute(
            "SELECT COUNT(*) FROM embeddings WHERE entry_id=?", (entry["id"],)
        ).fetchone()[0]
        status = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
    assert vectors == 0
    assert status == "superseded"


def test_reconcile_requeues_old_fingerprint_but_keeps_failed_jobs_terminal(
    tmp_path: Path,
) -> None:
    original_settings = Settings(
        data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake", model="fake-v1")
    )
    store = Store(original_settings)
    store.migrate()
    entry = store.create_entry(text="An entry with an old semantic index.")
    original_service = EmbeddingService(store, original_settings)
    vector = original_service._encode(["old passage"])[0]
    with store.connect() as db:
        job = db.execute(
            "SELECT id,revision FROM jobs WHERE entry_id=? AND kind='embed'",
            (entry["id"],),
        ).fetchone()
        assert job is not None
        _ = db.execute(
            "INSERT INTO embeddings(entry_id,revision,chunk_index,passage,vector,"
            "model_fingerprint,dimensions) VALUES (?,?,?,?,?,?,?)",
            (
                entry["id"],
                job["revision"],
                0,
                "old passage",
                vector.tobytes(),
                original_service.fingerprint,
                vector.size,
            ),
        )
        _ = db.execute("UPDATE jobs SET status='done' WHERE id=?", (job["id"],))

    changed_settings = Settings(
        data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake", model="fake-v2")
    )
    changed_service = EmbeddingService(Store(changed_settings), changed_settings)
    with store.connect() as db:
        changed_service.reconcile(db)
        queued = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        assert queued == "queued"
        _ = db.execute(
            "UPDATE jobs SET status='failed',last_error='terminal failure' WHERE id=?",
            (job["id"],),
        )
        changed_service.reconcile(db)
        failed = db.execute("SELECT status FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
    assert failed == "failed"


def test_disabling_embeddings_supersedes_jobs_and_reenable_queues_them(tmp_path: Path) -> None:
    enabled_settings = Settings(data_dir=tmp_path, embedding=EmbeddingSettings(provider="fake"))
    store = Store(enabled_settings)
    store.migrate()
    entry = store.create_entry(text="An entry awaiting semantic processing.")
    disabled_settings = Settings(data_dir=tmp_path, embedding=EmbeddingSettings(provider="none"))
    with store.connect() as db:
        EmbeddingService(store, disabled_settings).reconcile(db)
        disabled = db.execute(
            "SELECT status FROM jobs WHERE entry_id=? AND kind='embed'", (entry["id"],)
        ).fetchone()[0]
        EmbeddingService(store, enabled_settings).reconcile(db)
        enabled = db.execute(
            "SELECT status FROM jobs WHERE entry_id=? AND kind='embed'", (entry["id"],)
        ).fetchone()[0]
    assert disabled == "superseded"
    assert enabled == "queued"


@pytest.mark.skipif(os.getenv("MSJ_TEST_REAL_EMBEDDER") != "1", reason="opt-in model smoke test")
def test_real_bge_model_ranks_semantically_related_entry(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        embedding=EmbeddingSettings(provider="sentence-transformers", min_score=-1.0),
    )
    store = Store(settings)
    store.migrate()
    relevant = store.create_entry(text="I repaired a database index to speed up journal lookup.")
    _ = store.create_entry(text="I made a lemon cake and watered the houseplants.")
    service = EmbeddingService(store, settings)
    vector = service._encode(["I fixed a slow SQL query by optimizing its index."], query=True)[0]
    with store.connect() as db:
        job = db.execute(
            "SELECT id,entry_id,revision FROM jobs WHERE kind='embed' LIMIT 1"
        ).fetchone()
        assert job is not None
        token = "real-embed-smoke-claim"
        _ = db.execute(
            "UPDATE jobs SET status='running',claim_token=?,claim_protocol=4 WHERE id=?",
            (token, job["id"]),
        )
        db.commit()
        assert service.process(
            {
                "id": job["id"],
                "entry_id": job["entry_id"],
                "revision": job["revision"],
                "claim_token": token,
            }
        )
        second_job = db.execute(
            "SELECT id,entry_id,revision FROM jobs WHERE kind='embed' AND id!=? LIMIT 1",
            (job["id"],),
        ).fetchone()
        assert second_job is not None
        _ = db.execute(
            "UPDATE jobs SET status='running',claim_token=?,claim_protocol=4 WHERE id=?",
            (token + "-2", second_job["id"]),
        )
        db.commit()
        assert service.process(
            {
                "id": second_job["id"],
                "entry_id": second_job["entry_id"],
                "revision": second_job["revision"],
                "claim_token": token + "-2",
            }
        )
    rows, _ = service.search("I fixed a slow SQL query by optimizing its index.")
    assert rows[0]["entry_id"] == relevant["id"]
    assert vector.size == 384
