"""Queue and immutable pipeline behavior against isolated SQLite stores."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from msj.config import Settings, TaskKind, TaskRoute
from msj.pipeline import Runner
from msj.providers import Generation
from msj.storage import Store


class ScriptedProvider:
    def __init__(self, outputs: dict[TaskKind, str] | None = None) -> None:
        self.outputs = outputs or {}

    def generate(
        self,
        kind: TaskKind,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
        messages: Any = None,
    ) -> Generation:
        if kind == "transcribe":
            assert audio is not None
            return Generation(self.outputs.get(kind, "transcribed words"), "fake")
        if kind == "review":
            assert candidate is not None
        return Generation(self.outputs.get(kind, source), "fake")


def make_store(tmp_path: Path, **options: Any) -> tuple[Store, Settings]:
    defaults: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "tasks": {
            kind: TaskRoute(model="fake")
            for kind in ("transcribe", "cleanup", "review", "summarize", "extract")
        },
    }
    defaults.update(options)
    config = Settings(**defaults)
    store = Store(config)
    store.migrate()
    return store, config


def get_job(store: Store, entry_id: str, kind: str) -> dict[str, Any]:
    return next(
        job for job in store.list_jobs() if job["entry_id"] == entry_id and job["kind"] == kind
    )


def valid_extraction() -> str:
    return json.dumps(
        {
            "projects": [],
            "skills_technologies": [],
            "accomplishments": [],
            "people_roles": [],
            "star_fragments": [],
            "tags": [],
        }
    )


def test_text_pipeline_dependencies_artifacts_and_searchable_projection(tmp_path: Path) -> None:
    store, config = make_store(tmp_path)
    entry = store.create_entry(text="I completed a journal milestone.")
    assert {job["kind"] for job in store.list_jobs()} == {"cleanup", "summarize", "extract"}
    runner = Runner(
        store,
        config,
        ScriptedProvider(
            {
                "cleanup": "I completed a journal milestone.",
                "review": '{"unsupported_claims": []}',
                "summarize": "journal milestone",
                "extract": valid_extraction(),
            }
        ),
    )
    assert runner.run_batch() == {"done": 6, "failed": 0, "discarded": 0}
    latest = store.get_entry(entry["id"])
    assert {artifact["kind"] for artifact in latest["artifacts"]} == {
        "user_text",
        "cleaned",
        "summary",
        "extraction",
        "review",
    }
    assert all(
        artifact["model"] == "fake"
        for artifact in latest["artifacts"]
        if artifact["kind"] != "user_text"
    )
    assert all(job["status"] == "done" for job in store.list_jobs())
    assert "journal milestone" in store.search_documents()[0]["text"]


def test_audio_pipeline_orders_transcription_before_dependent_tasks(tmp_path: Path) -> None:
    store, config = make_store(tmp_path)
    entry = store.create_entry(audio=b"audio", extension="wav")
    runner = Runner(
        store,
        config,
        ScriptedProvider(
            {
                "cleanup": "clean words",
                "review": '{"unsupported_claims": []}',
                "summarize": "short words",
                "extract": valid_extraction(),
            }
        ),
    )
    assert runner.run_batch() == {"done": 7, "failed": 0, "discarded": 0}
    kinds = [job["kind"] for job in store.list_jobs() if job["entry_id"] == entry["id"]]
    assert kinds[0] == "transcribe"
    assert store.list_jobs("queued") == []
    assert {item["kind"] for item in store.get_entry(entry["id"])["artifacts"]} >= {
        "raw_audio",
        "raw_transcript",
        "cleaned",
    }


def test_review_flags_cleaned_artifact_and_invalid_extraction_reports_path(tmp_path: Path) -> None:
    store, config = make_store(tmp_path)
    entry = store.create_entry(text="I planned a release.")

    class TargetedReviewProvider(ScriptedProvider):
        def generate(
            self,
            kind: TaskKind,
            route: TaskRoute,
            source: str,
            *,
            candidate: str | None = None,
            audio: bytes | None = None,
            messages: Any = None,
        ) -> Generation:
            if kind == "review":
                claims = ["shipped"] if candidate == "I shipped a release." else []
                return Generation(json.dumps({"unsupported_claims": claims}), "fake")
            if messages is not None:
                return Generation(source, "fake")
            return super().generate(
                kind, route, source, candidate=candidate, audio=audio, messages=messages
            )

    result = Runner(
        store,
        config,
        TargetedReviewProvider(
            {
                "cleanup": "I shipped a release.",
                "review": '{"unsupported_claims": ["shipped"]}',
                "summarize": "planned a release",
                "extract": '{"projects": []}',
            }
        ),
    ).run_batch()
    assert result == {"done": 6, "failed": 1, "discarded": 0}
    latest = store.get_entry(entry["id"])
    cleanup_session = latest["review_sessions"]["cleanup"]
    assert cleanup_session["status"] == "accepted"
    assert cleanup_session["turns_used"] == 2
    cleaned = cleanup_session["final_artifact"]
    assert cleaned["content"] == "I planned a release."
    extract_job = get_job(store, entry["id"], "extract")
    assert extract_job["status"] == "queued"
    assert "validation failed at $" in get_job(store, entry["id"], "extract")["last_error"]


def test_failure_backoff_max_attempts_and_manual_retry(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, max_attempts=2, retry_base_s=10)
    entry = store.create_entry(text="text")
    job_id = get_job(store, entry["id"], "cleanup")["id"]
    claim = store.claim({job_id})
    assert claim is not None and claim["attempts"] == 1
    assert store.fail_job(claim, "temporary provider error") is True
    queued = get_job(store, entry["id"], "cleanup")
    assert queued["status"] == "queued" and queued["last_error"] == "temporary provider error"
    assert queued["not_before"] >= time.time() + 9
    with store.connect() as db:
        _ = db.execute("UPDATE jobs SET not_before=? WHERE id=?", (time.time() - 1, job_id))
    second = store.claim({job_id})
    assert second is not None and second["attempts"] == 2
    assert store.fail_job(second, "still failing") is True
    terminal = get_job(store, entry["id"], "cleanup")
    assert terminal["status"] == "failed"
    retried = store.retry_job(job_id)
    assert retried["status"] == "queued" and retried["attempts"] == 0
    assert retried["last_error"] is None


def test_patch_invalidates_claim_and_versions_artifacts(tmp_path: Path) -> None:
    store, config = make_store(tmp_path)
    entry = store.create_entry(text="original source")
    old_claim = store.claim({get_job(store, entry["id"], "cleanup")["id"]})
    assert old_claim is not None
    assert store.patch_entry(entry["id"], {"text": "revised source"})["revision"] == 2
    assert store.finish_job(old_claim, "stale result", "fake", "hash") is False
    outputs = {
        "cleanup": "revised clean",
        "review": '{"unsupported_claims": []}',
        "summarize": "revised summary",
        "extract": valid_extraction(),
    }
    assert Runner(store, config, ScriptedProvider(outputs)).run_batch()["done"] == 6
    history = store.get_entry(entry["id"], history=True)["artifacts"]
    old_text = next(
        item for item in history if item["kind"] == "user_text" and item["revision"] == 1
    )
    new_text = next(
        item for item in history if item["kind"] == "user_text" and item["revision"] == 2
    )
    assert old_text["content"] == "original source" and old_text["superseded_by"] == new_text["id"]
    assert not any(item["content"] == "stale result" for item in history)
    assert all(item["revision"] == 2 for item in store.get_entry(entry["id"])["artifacts"])


def test_expired_lease_is_recovered_on_claim(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    entry = store.create_entry(text="lease test")
    job_id = get_job(store, entry["id"], "cleanup")["id"]
    original = store.claim({job_id})
    assert original is not None
    with store.connect() as db:
        _ = db.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job_id))
    recovered = store.claim({job_id})
    assert recovered is not None and recovered["attempts"] == 2
    assert recovered["claim_protocol"] == 4
    assert recovered["claim_token"] != original["claim_token"]
    assert store.finish_job(original, "stale result", "fake", "sha") is False
    assert "lease expired" in recovered["last_error"]


def test_persisted_queue_and_concurrent_claim_are_unique(tmp_path: Path) -> None:
    store, config = make_store(tmp_path)
    entry = store.create_entry(text="persist me")
    reopened = Store(config)
    job_id = get_job(reopened, entry["id"], "cleanup")["id"]
    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda _: reopened.claim({job_id}), range(8)))
    assert len([claim for claim in claims if claim is not None]) == 1
    persisted = get_job(Store(config), entry["id"], "cleanup")
    assert persisted["status"] == "running" and persisted["attempts"] == 1


def test_audio_delete_has_durable_cleanup_after_filesystem_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    import pytest

    from msj.storage import NotFound

    store, config = make_store(tmp_path)
    entry = store.create_entry(audio=b"retained until deletion", extension="wav")
    audio_path = store.data_dir / entry["artifacts"][0]["content"]
    unlink = Path.unlink

    def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == audio_path:
            raise OSError("injected filesystem failure")
        unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "unlink", fail_unlink)
        store.delete_entry(entry["id"])
    with pytest.raises(NotFound):
        store.get_entry(entry["id"])
    assert audio_path.read_bytes() == b"retained until deletion"
    Store(config).migrate()
    assert not audio_path.exists()
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM file_deletions").fetchone()[0] == 0


def test_batch_groups_all_entries_by_task_endpoint(tmp_path: Path) -> None:
    import httpx

    from msj.config import TASKS
    from msj.providers import InferenceProvider

    routes = {
        kind: TaskRoute(
            provider="fake", model={"transcribe": "whisper", "cleanup": "phi"}.get(kind, "gemma")
        )
        for kind in TASKS
    }
    store, config = make_store(tmp_path, tasks=routes)
    _ = store.create_entry(audio=b"one", extension="wav")
    _ = store.create_entry(audio=b"two", extension="wav")
    _ = store.create_entry(text="three")
    seen: list[str] = []
    with httpx.Client() as http:
        delegate = InferenceProvider(config, http)

        class RecordingProvider:
            def generate(
                self,
                kind: TaskKind,
                route: TaskRoute,
                source: str,
                *,
                candidate: str | None = None,
                audio: bytes | None = None,
                messages: Any = None,
            ) -> Generation:
                seen.append(route.model)
                return delegate.generate(kind, route, source, candidate=candidate, audio=audio)

        assert Runner(store, config, RecordingProvider()).run_batch()["done"] == 20
    assert seen == ["whisper"] * 2 + ["phi"] * 3 + ["gemma"] * 15
