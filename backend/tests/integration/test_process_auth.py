"""Bearer, browser-session, CSRF, and login rate limiting against a real `msj serve`."""

import pytest

from .msj_harness import MsjFactory, MsjInstance

pytestmark = pytest.mark.integration

COOKIE = "msj_session"  # insecure_cookies = true selects the unprefixed name


@pytest.fixture
def server(msj_factory: MsjFactory) -> MsjInstance:
    return msj_factory().boot(worker=False)


def test_bearer_token_authenticates_and_bad_bearer_is_rejected(server: MsjInstance) -> None:
    _ = server.expect(server.api.get("/api/v1/session"), 204)
    listing = server.expect(server.api.get("/api/v1/entries"), 200)
    assert listing == {"items": [], "total": 0}

    anonymous = server.client(authenticated=False)
    _ = server.expect_problem(anonymous.get("/api/v1/entries"), 401, "UNAUTHORIZED")
    _ = server.expect_problem(anonymous.get("/api/v1/session"), 401, "UNAUTHORIZED")

    impostor = server.client(bearer="not-the-token")
    _ = server.expect_problem(impostor.get("/api/v1/entries"), 401, "UNAUTHORIZED")
    _ = server.expect_problem(
        impostor.post("/api/v1/entries", json={"text": "Should not be stored."}),
        401,
        "UNAUTHORIZED",
    )
    assert server.expect(server.api.get("/api/v1/entries"), 200)["total"] == 0


def test_session_cookie_lifecycle_and_csrf(server: MsjInstance) -> None:
    assert server.token is not None
    anonymous = server.client(authenticated=False)
    _ = server.expect_problem(
        anonymous.post("/api/v1/session", json={"token": "wrong"}), 401, "BAD_TOKEN"
    )
    _ = server.expect_problem(anonymous.post("/api/v1/session", json={}), 401, "BAD_TOKEN")

    login = anonymous.post("/api/v1/session", json={"token": server.token})
    _ = server.expect(login, 204)
    session_id = login.cookies.get(COOKIE)
    assert session_id, f"no {COOKIE} cookie in {login.headers.get_list('set-cookie')}"
    assert session_id != server.token, "the cookie must not carry the bearer token"
    set_cookie = ";".join(login.headers.get_list("set-cookie")).lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie

    browser = server.client(cookies={COOKIE: session_id}, authenticated=False)
    _ = server.expect(browser.get("/api/v1/session"), 204)
    assert server.expect(browser.get("/api/v1/entries"), 200)["total"] == 0

    _ = server.expect_problem(
        browser.post("/api/v1/entries", json={"text": "No CSRF header."}),
        403,
        "CSRF_HEADER_MISSING",
    )
    assert server.expect(server.api.get("/api/v1/entries"), 200)["total"] == 0
    created = server.expect(
        browser.post(
            "/api/v1/entries", json={"text": "With CSRF header."}, headers={"X-MSJ-CSRF": "1"}
        ),
        201,
    )
    _ = server.expect_problem(
        browser.patch(f"/api/v1/entries/{created['id']}", json={"title": "x"}),
        403,
        "CSRF_HEADER_MISSING",
    )
    _ = server.expect_problem(
        browser.delete(f"/api/v1/entries/{created['id']}"), 403, "CSRF_HEADER_MISSING"
    )
    assert server.entry(created["id"])["title"] is None

    _ = server.expect(browser.delete("/api/v1/session", headers={"X-MSJ-CSRF": "1"}), 204)
    # The server revoked the session: replaying the old cookie no longer authenticates.
    replay = server.client(cookies={COOKIE: session_id}, authenticated=False)
    _ = server.expect_problem(replay.get("/api/v1/session"), 401, "UNAUTHORIZED")
    _ = server.expect_problem(replay.get("/api/v1/entries"), 401, "UNAUTHORIZED")
    # Bearer access is unaffected by signing a browser session out.
    _ = server.expect(server.api.get("/api/v1/session"), 204)


def test_login_rate_limit_returns_retry_after(msj_factory: MsjFactory) -> None:
    server = msj_factory({"login_rate_limit": 2, "login_rate_window_s": 600}).boot(worker=False)
    assert server.token is not None
    anonymous = server.client(authenticated=False)
    for _ in range(2):
        _ = server.expect_problem(
            anonymous.post("/api/v1/session", json={"token": "wrong"}), 401, "BAD_TOKEN"
        )
    limited = anonymous.post("/api/v1/session", json={"token": "wrong"})
    _ = server.expect_problem(limited, 429, "RATE_LIMITED")
    retry_after = limited.headers.get("retry-after")
    assert retry_after is not None, f"429 without Retry-After: {dict(limited.headers)}"
    assert retry_after.isdigit()
    assert 0 < int(retry_after) <= 600
    # The bucket is per client address, so even the right token waits out the window.
    _ = server.expect_problem(
        anonymous.post("/api/v1/session", json={"token": server.token}), 429, "RATE_LIMITED"
    )
    # Bearer authentication has its own bucket and keeps working.
    _ = server.expect(server.api.get("/api/v1/entries"), 200)
