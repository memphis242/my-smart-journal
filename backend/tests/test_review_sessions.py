"""Persistent bounded review sessions cover cleanup, summaries, and extraction."""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from msj.config import PRODUCERS, TASKS, Settings, TaskRoute
from msj.pipeline import Runner
from msj.providers import Generation
from msj.storage import Store


def valid_extraction(project: str = "Atlas") -> str:
    return json.dumps(
        {
            "projects": [project],
            "skills_technologies": [],
            "accomplishments": [],
            "people_roles": [],
            "star_fragments": [],
            "tags": [],
        }
    )


class ReviewScenario:
    """A deterministic producer/reviewer that records public call inputs."""

    def __init__(
        self,
        claims: dict[str, list[list[str]]] | None = None,
        revisions: dict[str, list[str]] | None = None,
    ) -> None:
        self.claims = claims or {}
        self.revisions = revisions or {}
        self.reviewed: dict[str, int] = defaultdict(int)
        self.target_by_candidate: dict[str, str] = {}
        self.producer_sources: dict[str, list[str]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []
        self.on_review: Any = None

    def generate(
        self,
        kind: str,
        route: TaskRoute,
        source: str,
        *,
        candidate: str | None = None,
        audio: bytes | None = None,
        messages: Any = None,
        **context: Any,
    ) -> Generation:
        if kind == "review":
            assert candidate is not None
            target = self.target_by_candidate[candidate]
            self.reviewed[target] += 1
            turn = self.reviewed[target]
            self.calls.append(
                {
                    "kind": kind,
                    "target": target,
                    "turn": turn,
                    "source": source,
                    "candidate": candidate,
                    "messages": messages,
                    "context": context,
                    "model": route.model,
                }
            )
            claims = self.claims.get(target, [])
            unsupported = claims[min(turn - 1, len(claims) - 1)] if claims else []
            if self.on_review is not None:
                self.on_review(target, turn)
            return Generation(json.dumps({"unsupported_claims": unsupported}), "review-model")

        if messages is not None:
            assert kind in PRODUCERS
            target = kind
            self.calls.append(
                {
                    "kind": kind,
                    "target": target,
                    "source": source,
                    "messages": messages,
                    "candidate": candidate,
                    "context": context,
                    "model": route.model,
                }
            )
            revised = self.revisions.get(target, [])
            prior_versions = sum(
                1
                for call in self.calls
                if call["kind"] == target and call.get("messages") is not None
            )
            output = (
                revised[prior_versions - 1]
                if prior_versions <= len(revised)
                else (f"{target} corrected output {prior_versions}")
            )
            self.target_by_candidate[output] = target
            return Generation(output, f"{target}-model")

        if kind == "transcribe":
            assert audio is not None
            output = "raw transcript source"
        else:
            assert kind in PRODUCERS
            self.producer_sources[kind].append(source)
            output = {
                "cleanup": "cleaned v1",
                "summarize": "summary v1",
                "extract": valid_extraction(),
            }[kind]
        self.target_by_candidate[output] = kind
        self.calls.append(
            {
                "kind": kind,
                "target": kind,
                "source": source,
                "candidate": None,
                "messages": None,
                "context": context,
                "model": route.model,
            }
        )
        return Generation(output, f"{kind}-model")


def make_store(
    tmp_path: Path,
    *,
    provider: ReviewScenario | None = None,
    max_turns: int = 3,
    enabled_for: tuple[str, ...] = PRODUCERS,
    task_models: dict[str, str] | None = None,
) -> tuple[Store, Settings, Runner, ReviewScenario]:
    settings = Settings(
        data_dir=tmp_path / "journal",
        review={"max_turns": max_turns, "enabled_for": enabled_for},
        tasks={
            kind: TaskRoute(provider="fake", model=(task_models or {}).get(kind, "fake"))
            for kind in TASKS
        },
    )
    store = Store(settings)
    store.migrate()
    scripted = provider or ReviewScenario()
    return store, settings, Runner(store, settings, scripted), scripted


def sessions(store: Store, entry_id: str) -> dict[str, dict[str, Any]]:
    return store.get_entry(entry_id)["review_sessions"]


def producer_history(store: Store, entry_id: str, kind: str) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in store.get_entry(entry_id, history=True)["artifacts"]
        if artifact["kind"] == kind
    ]


def test_each_producer_is_accepted_on_its_first_review_turn(tmp_path: Path) -> None:
    store, _, runner, provider = make_store(tmp_path)
    entry = store.create_entry(text="I worked on Atlas and completed one migration.")

    result = runner.run_batch()

    assert result["failed"] == 0
    assert {
        target: session["status"] for target, session in sessions(store, entry["id"]).items()
    } == {"cleanup": "accepted", "summarize": "accepted", "extract": "accepted"}
    assert all(session["turns_used"] == 1 for session in sessions(store, entry["id"]).values())
    assert all(
        session["remaining_claims"] == [] for session in sessions(store, entry["id"]).values()
    )
    assert {call["target"] for call in provider.calls if call["kind"] == "review"} == set(PRODUCERS)
    for target, session in sessions(store, entry["id"]).items():
        assert session["final_artifact"]["id"]
        assert len(session["history"]) == 1
        review = session["history"][0]
        assert review["target"] == target
        assert review["turn"] == 1
        assert review["reviewed_artifact_id"] == session["final_artifact"]["id"]
        assert review["model"] == "review-model"
        assert len(review["prompt_sha256"]) == 64


def test_claims_then_clean_revision_keeps_version_chain_and_final_sources(tmp_path: Path) -> None:
    provider = ReviewScenario(
        claims={"cleanup": [["'Atlas shipped' is not supported"], []]},
        revisions={"cleanup": ["cleaned v2"]},
    )
    store, _, runner, _ = make_store(
        tmp_path, provider=provider, task_models={"cleanup": "phi-cleanup"}
    )
    entry = store.create_entry(text="I planned an Atlas release.")

    result = runner.run_batch()

    assert result["failed"] == 0
    history = producer_history(store, entry["id"], "cleaned")
    assert [artifact["content"] for artifact in history] == ["cleaned v1", "cleaned v2"]
    assert history[0]["superseded_by"] == history[1]["id"]
    cleanup = sessions(store, entry["id"])["cleanup"]
    assert cleanup["status"] == "accepted"
    assert cleanup["turns_used"] == 2
    assert cleanup["final_artifact"]["id"] == history[1]["id"]
    assert [review["turn"] for review in cleanup["history"]] == [1, 2]
    assert [review["reviewed_artifact_id"] for review in cleanup["history"]] == [
        history[0]["id"],
        history[1]["id"],
    ]
    revision_call = next(
        call
        for call in provider.calls
        if call["kind"] == "cleanup" and call["messages"] is not None
    )
    assert revision_call["model"] == "phi-cleanup"
    assert revision_call["source"] == "I planned an Atlas release."
    assert [message["role"] for message in revision_call["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert provider.producer_sources["summarize"] == ["cleaned v2"]
    assert provider.producer_sources["extract"] == ["cleaned v2"]
    assert result["done"] == len(store.list_jobs())


def test_claims_after_all_three_turns_keep_latest_and_attention_claims(tmp_path: Path) -> None:
    claims = [[f"unsupported candidate claim {turn}"] for turn in range(1, 4)]
    provider = ReviewScenario(
        claims={"summarize": claims}, revisions={"summarize": ["summary v2", "summary v3"]}
    )
    store, _, runner, _ = make_store(tmp_path, provider=provider)
    entry = store.create_entry(text="The source describes a tentative plan.")

    result = runner.run_batch()

    summary = sessions(store, entry["id"])["summarize"]
    assert result["failed"] == 0
    assert summary["status"] == "needs_attention"
    assert summary["turns_used"] == 3
    assert summary["remaining_claims"] == ["unsupported candidate claim 3"]
    assert summary["final_artifact"]["content"] == "summary v3"
    assert bool(summary["final_artifact"]["needs_attention"])
    assert [review["turn"] for review in summary["history"]] == [1, 2, 3]
    versions = producer_history(store, entry["id"], "summary")
    assert [item["content"] for item in versions] == ["summary v1", "summary v2", "summary v3"]
    assert versions[0]["superseded_by"] == versions[1]["id"]
    assert versions[1]["superseded_by"] == versions[2]["id"]


def test_invalid_revised_extraction_fails_the_revise_job_without_publishing_it(
    tmp_path: Path,
) -> None:
    invalid_output = json.loads(valid_extraction())
    invalid_output["projects"] = 123
    provider = ReviewScenario(
        claims={"extract": [["unsupported project claim"], []]},
        revisions={"extract": [json.dumps(invalid_output)]},
    )
    store, _, runner, _ = make_store(tmp_path, provider=provider)
    entry = store.create_entry(text="I considered Atlas.")

    result = runner.run_batch()

    jobs = [job for job in store.list_jobs() if job["entry_id"] == entry["id"]]
    failure = next(job for job in jobs if job["kind"] == "revise" and job["target"] == "extract")
    assert result["failed"] == 1
    assert failure["status"] == "failed"
    assert " at $.projects" in failure["last_error"]
    assert "unsupported project claim" not in failure["last_error"]
    versions = producer_history(store, entry["id"], "extraction")
    assert len(versions) == 1
    assert versions[0]["content"] == valid_extraction()
    assert sessions(store, entry["id"])["extract"]["status"] == "pending"


def test_review_disabled_for_a_producer_records_disabled_session(tmp_path: Path) -> None:
    store, _, runner, _ = make_store(tmp_path, enabled_for=("cleanup", "extract"))
    entry = store.create_entry(text="I worked on Atlas.")

    result = runner.run_batch()

    assert result["failed"] == 0
    review_sessions = sessions(store, entry["id"])
    assert review_sessions["cleanup"]["status"] == "accepted"
    assert review_sessions["summarize"]["status"] == "disabled"
    assert review_sessions["summarize"]["final_artifact"]["content"] == "summary v1"
    assert review_sessions["summarize"]["turns_used"] == 0
    assert result["done"] == 5


def test_edit_mid_session_supersedes_claimed_review_and_bounds_the_batch(
    tmp_path: Path,
) -> None:
    provider = ReviewScenario(claims={"cleanup": [["unsupported claim"], []]})
    store, _, runner, _ = make_store(tmp_path, provider=provider)
    entry = store.create_entry(text="Original journal source.")
    changed = False

    def edit_after_review(target: str, turn: int) -> None:
        nonlocal changed
        if target == "cleanup" and turn == 1 and not changed:
            changed = True
            _ = store.patch_entry(entry["id"], {"text": "Edited during review."})

    provider.on_review = edit_after_review

    result = runner.run_batch()

    assert changed
    assert result["failed"] == 0
    assert result["discarded"] == 1
    current = store.get_entry(entry["id"])
    assert current["revision"] == 2
    jobs = store.list_jobs()
    old_revision = [job for job in jobs if job["revision"] == 1]
    old_cleanup_review = next(
        job for job in old_revision if job["kind"] == "review" and job.get("target") == "cleanup"
    )
    assert all(job["status"] in {"done", "superseded"} for job in old_revision)
    assert next(job for job in old_revision if job["id"] == old_cleanup_review["id"])["status"] == (
        "superseded"
    )
    new_revision = [job for job in jobs if job["revision"] == 2]
    assert new_revision
    assert all(job["status"] == "queued" for job in new_revision)
    assert not any(
        artifact["revision"] == 1 and artifact["kind"] == "review"
        for artifact in store.get_entry(entry["id"], history=True)["artifacts"]
    )
