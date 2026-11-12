"""Export, compilations, optimistic PATCH versions, and deletion cascades on a real server."""

import json

import pytest

from .msj_harness import MsjInstance

pytestmark = pytest.mark.integration

FIRST = "Mapped the Orion release checklist with the QA team."
SECOND = "Refactored the billing retry queue and wrote load tests."


def test_export_formats(msj: MsjInstance) -> None:
    first = msj.create_text_entry(FIRST, title="Orion", tags=["release"], entry_date="2026-10-01")
    second = msj.create_text_entry(SECOND, title="Billing", entry_date="2026-10-02")
    msj.process_entries([first["id"], second["id"]])
    ids = [first["id"], second["id"]]

    for format_name, media_type, extension in (
        ("text", "text/plain", "txt"),
        ("markdown", "text/markdown", "md"),
        ("json", "application/json", "json"),
    ):
        response = msj.api.post(
            "/api/v1/export", json={"entry_ids": ids, "format": format_name, "order": "asc"}
        )
        _ = msj.expect(response, 200)
        assert response.headers["content-type"].startswith(media_type), response.headers
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;"), disposition
        assert disposition.endswith(f'.{extension}"'), disposition
        assert response.headers["cache-control"] == "no-store"
        body = response.text
        assert body.index(FIRST) < body.index(SECOND), "order=asc puts the older entry first"
        if format_name == "json":
            exported = json.loads(body)
            assert [item["id"] for item in exported] == ids
            assert exported[0]["tags"] == ["release"]
            assert exported[0]["entry_date"] == "2026-10-01"

    _ = msj.expect_problem(
        msj.api.post(
            "/api/v1/export", json={"entry_ids": [*ids, "missing-entry"], "format": "json"}
        ),
        404,
        "ENTRY_NOT_FOUND",
    )


def test_compilation_stale_patch_and_delete_cascade(msj: MsjInstance) -> None:
    first = msj.create_text_entry(FIRST)
    second = msj.create_text_entry(SECOND)
    msj.process_entries([first["id"], second["id"]])

    created = msj.expect(
        msj.api.post(
            "/api/v1/compilations",
            json={
                "entry_ids": [first["id"], second["id"]],
                "purpose": "resume bullets",
                "period": "2026 Q4",
            },
        ),
        202,
    )
    assert created["status"] == "queued"
    assert created["entry_ids"] == [first["id"], second["id"]]
    compilation_url = f"/api/v1/compilations/{created['id']}"
    msj.process_until(
        lambda: msj.expect(msj.api.get(compilation_url), 200)["status"] == "done",
        what=f"compilation {created['id']} to reach status done",
    )
    compilation = msj.expect(msj.api.get(compilation_url), 200)
    assert compilation["result"] is not None
    assert compilation["model"] == "fake"
    assert compilation["prompt_sha256"]
    assert compilation["last_error"] is None
    assert msj.entry(first["id"])["compilation_count"] == 1

    # Optimistic concurrency: a PATCH based on an old version is refused atomically.
    entry_url = f"/api/v1/entries/{first['id']}"
    patched = msj.expect(
        msj.api.patch(entry_url, json={"title": "Orion", "expected_version": 1}), 200
    )
    assert patched["version"] == 2
    stale = msj.expect_problem(
        msj.api.patch(entry_url, json={"title": "Lost update", "expected_version": 1}),
        409,
        "ENTRY_CHANGED",
    )
    assert stale["current_version"] == 2
    current = msj.entry(first["id"])
    assert current["title"] == "Orion"
    assert current["version"] == 2

    # Deleting a cited entry removes the compilation that cites it.
    _ = msj.expect(msj.api.delete(entry_url), 204)
    _ = msj.expect_problem(msj.api.get(entry_url), 404, "NOT_FOUND")
    _ = msj.expect_problem(msj.api.get(compilation_url), 404, "NOT_FOUND")
    remaining = msj.expect(msj.api.get("/api/v1/compilations"), 200)
    assert all(item["id"] != created["id"] for item in remaining)
    survivor = msj.entry(second["id"])
    assert survivor["compilation_count"] == 0
    assert survivor["status"] == "done"
    listing = msj.expect(msj.api.get("/api/v1/entries"), 200)
    assert [item["id"] for item in listing["items"]] == [second["id"]]
