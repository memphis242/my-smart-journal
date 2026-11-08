import json

import httpx
import pytest

from msj_bench.runpod_client import BenchmarkError, RunPodClient


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, value):
        self.sleeps.append(value)
        self.now += value


def client(http, clock, **kwargs):
    return RunPodClient(http, api_key="fake-offline-credential", endpoint_id="test-endpoint", api_base="https://example.test/v2",
                        clock=clock.clock, sleep=clock.sleep, **kwargs)


def test_submit_poll_backoff_and_metadata():
    states = iter([{"id": "job", "status": "IN_QUEUE"}, {"status": "IN_PROGRESS"},
                   {"status": "COMPLETED", "output": {"text": "hello"}, "delayTime": 100,
                    "executionTime": 200, "workerId": "worker"}])
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=next(states))
    clock = FakeClock()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = client(http, clock, poll_initial_s=1, backoff=2, poll_max_s=2).run({"audio": "https://example.test/clip.wav"})
    assert [r.url.path for r in requests] == ["/v2/test-endpoint/run", "/v2/test-endpoint/status/job", "/v2/test-endpoint/status/job"]
    assert json.loads(requests[0].content) == {"input": {"audio": "https://example.test/clip.wav"}}
    assert clock.sleeps == [1, 2]
    assert (result.delay_time_ms, result.execution_time_ms, result.wall_time_s, result.worker_id) == (100, 200, 3, "worker")


@pytest.mark.parametrize("cancel_fails", [False, True])
def test_timeout_cancels_once(cancel_fails):
    requests = []
    def handler(request):
        requests.append(request)
        if "/cancel/" in request.url.path:
            return httpx.Response(503 if cancel_fails else 200, json={"status": "CANCELLED"})
        return httpx.Response(200, json={"id": "job", "status": "IN_QUEUE"})
    clock = FakeClock()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BenchmarkError, match="cancellation failed" if cancel_fails else "cancellation requested"):
            client(http, clock, timeout_s=2).run({})
    assert clock.now == 2
    assert len([r for r in requests if "/cancel/" in r.url.path]) == 1


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "TIMED_OUT"])
def test_failure_status_is_actionable(status):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "job", "status": status, "error": "private remote body"}))) as http:
        with pytest.raises(BenchmarkError, match=status) as caught:
            client(http, FakeClock()).run({})
    assert "private remote body" not in str(caught.value)


def test_poll_network_failure_cancels_without_reflecting_secrets():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if "/status/" in request.url.path:
            raise httpx.ReadTimeout("do not print this secret")
        return httpx.Response(200, json={"id": "job", "status": "IN_QUEUE"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BenchmarkError, match="connectivity") as caught:
            client(http, FakeClock()).run({})
    assert requests[-1].endswith("/cancel/job")
    assert "secret" not in str(caught.value)


def test_paused_endpoint_error_includes_provider_detail_and_remedy(caplog):
    body = {"status": 409, "title": "Conflict", "detail": "Endpoint is paused (max_workers=0). Set max_workers > 0 to accept work.", "code": "ENDPOINT_PAUSED"}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(409, json=body))) as http:
        with pytest.raises(BenchmarkError) as caught:
            client(http, FakeClock()).run({"private": "journal text"})
    message = str(caught.value)
    assert "HTTP 409" in message and "ENDPOINT_PAUSED" in message
    assert body["detail"] in message
    assert "max workers >= 1" in message
    assert "test-endpoint" in message
    assert "journal text" not in message
    assert "ENDPOINT_PAUSED" in caplog.text
    assert body["detail"] in caplog.text


def test_unauthorized_error_names_key_without_secret():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"code": "INVALID_KEY"}))) as http:
        with pytest.raises(BenchmarkError) as caught:
            client(http, FakeClock()).run({})
    assert "RUNPOD_API_KEY" in str(caught.value)
    assert "fake-offline-credential" not in str(caught.value)


def test_non_json_server_error_keeps_status():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(502, text="upstream unavailable"))) as http:
        with pytest.raises(BenchmarkError) as caught:
            client(http, FakeClock()).run({})
    assert "HTTP 502" in str(caught.value)
    assert "test-endpoint" in str(caught.value)


def test_invalid_submission_and_json():
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "IN_QUEUE"}))) as http:
        with pytest.raises(BenchmarkError, match="job ID"):
            client(http, FakeClock()).run({})
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not JSON"))) as http:
        with pytest.raises(BenchmarkError, match="invalid JSON"):
            client(http, FakeClock()).run({})


def test_unknown_status_cancels_a_potentially_active_job():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": "job", "status": "UNRECOGNIZED"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BenchmarkError, match="unknown job status"):
            client(http, FakeClock()).run({})
    assert paths[-1].endswith("/cancel/job")


@pytest.mark.parametrize("status", [None, [], {}])
def test_malformed_status_cancels_with_clear_error(status):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": "job", "status": status})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BenchmarkError, match="job status"):
            client(http, FakeClock()).run({})
    assert paths[-1].endswith("/cancel/job")
