import base64
import json

import httpx
import pytest

from msj_bench.backends import OpenAIBackend, RunPodLLM, WhisperBackend, parse_chat
from msj_bench.runpod_client import BenchmarkError, RunPodClient
from msj_bench.tasks import REVIEW_SCHEMA, messages_for


def test_llm_wire_format_local_and_runpod():
    requests = []
    reply = {"model": "actual-gguf", "choices": [{"message": {"content": '{"unsupported_claims": []}'}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 40, "completion_tokens": 8}}
    def handler(request):
        requests.append(request)
        if request.url.host == "pod.test":
            return httpx.Response(200, json={"id": "job", "status": "COMPLETED", "output": reply, "executionTime": 50})
        return httpx.Response(200, json=reply)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        pod = RunPodClient(http, api_key="offline-fake", endpoint_id="test", api_base="https://pod.test")
        messages = messages_for("review", "A source", candidate="A candidate")
        result = RunPodLLM(pod).generate(messages, max_tokens=77, schema=REVIEW_SCHEMA)
        local = OpenAIBackend(http, base_url="http://cpu.test/v1/").generate(messages, max_tokens=77, schema=REVIEW_SCHEMA)
    assert result.model == local.model == "actual-gguf"
    assert result.completion_tokens == 8
    body = json.loads(requests[0].content)["input"]
    assert body["openai_route"] == "/v1/chat/completions"
    assert body["openai_input"]["max_tokens"] == 77
    assert body["openai_input"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert body["openai_input"]["response_format"]["json_schema"]["schema"] == REVIEW_SCHEMA
    local_body = json.loads(requests[1].content)
    assert local_body["chat_template_kwargs"]["enable_thinking"] is False
    assert "extra_body" not in local_body
    assert requests[1].url.path == "/v1/chat/completions"


def test_whisper_file_base64_and_url(tmp_path):
    path = tmp_path / "fake.wav"
    _written = path.write_bytes(b"fictional-offline-audio")
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content)["input"])
        return httpx.Response(200, json={"id": "job", "status": "COMPLETED", "output": {"segments": [{"text": " hello "}, {"text": "world"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        backend = WhisperBackend(RunPodClient(http, api_key="offline-fake", endpoint_id="test"))
        assert backend.transcribe(path).text == "hello world"
        assert backend.transcribe("https://audio.test/clip.wav").text == "hello world"
    assert base64.b64decode(bodies[0]["audio_base64"]) == path.read_bytes()
    assert "audio" not in bodies[0]
    assert bodies[1]["audio"] == "https://audio.test/clip.wav"
    assert bodies[1]["model"] == "turbo"


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                                  {"choices": [{"message": {"content": "ok"}}], "usage": {"completion_tokens": -1}}])
def test_malformed_chat_is_error(data):
    with pytest.raises(BenchmarkError):
        parse_chat(data, 1)
