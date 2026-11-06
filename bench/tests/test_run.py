import json
from pathlib import Path

import httpx
import pytest

from msj_bench.config import load_config, output_root
from msj_bench.datasets import BENCH_ROOT
from msj_bench.run import main
from msj_bench.runpod_client import BenchmarkError


def test_cli_llm_all_tasks_and_comparison_offline(tmp_path, monkeypatch, empty_extraction):
    dataset = tmp_path / "input.json"
    _written = dataset.write_text(json.dumps({"id": "fiction", "raw_transcript": "Um, Mira tested v2 at 3.5 V.",
                                           "reference_cleaned": "Mira tested v2 at 3.5 V.", "key_facts": ["Mira", "v2", "3.5 V"]}))
    requests = []
    outputs = iter(["Mira tested v2 at 3.5 V.", "Mira tested v2.", json.dumps(empty_extraction), '{"unsupported_claims": []}'])
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "server-model", "choices": [{"message": {"content": next(outputs)}, "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 30, "completion_tokens": 10}})
    real_client = httpx.Client
    monkeypatch.setattr("msj_bench.run.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    code = main(["llm", "--set", str(dataset), "--label", "fiction-model", "--backend", "openai", "--out-dir", str(tmp_path / "results")])
    assert code == 0
    directory, = (tmp_path / "results").iterdir()
    results = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
    assert len(results) == len(requests) == 4
    assert {r["task"] for r in results} == {"cleanup", "summarize", "extract", "review"}
    assert all(r["server_model"] == "server-model" and r["performance"]["estimated_cost_usd"] is None for r in results)
    review_input = json.loads(requests[-1]["messages"][-1]["content"])
    assert review_input["candidate"] == results[0]["output"]
    assert json.loads((directory / "summary.json").read_text())["server_models"] == ["server-model"]
    assert main(["report", str(directory), "--out-dir", str(tmp_path / "comparisons")]) == 0
    comparison, = (tmp_path / "comparisons").iterdir()
    assert len(list(comparison.glob("ratings-*.csv"))) == 4


def test_cli_stt_mocked_runpod(tmp_path, monkeypatch):
    _written = (tmp_path / "one.wav").write_bytes(b"fake")
    _written = (tmp_path / "one.txt").write_text("Mira tested 3.5 V.")
    monkeypatch.setenv("RUNPOD_API_KEY", "fake-offline-key")
    real_client = httpx.Client
    def handler(request):
        assert "audio_base64" in json.loads(request.content)["input"]
        return httpx.Response(200, json={"id": "job", "status": "COMPLETED", "delayTime": 1000, "executionTime": 2000,
                                       "workerId": "worker", "output": {"transcription": "Mira tested 3.5 V.", "model": "turbo"}})
    monkeypatch.setattr("msj_bench.run.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert main(["stt", "--set", str(tmp_path), "--label", "whisper", "--out-dir", str(tmp_path / "outputs")]) == 0
    output, = (tmp_path / "outputs").iterdir()
    result = json.loads((output / "results.jsonl").read_text())
    assert result["metrics"] == {"wer": 0, "cer": 0}
    assert result["performance"]["estimated_cost_usd"] == pytest.approx(3 * .58 / 3600)


def test_cli_failure_is_recorded_and_remaining_tasks_continue(tmp_path, monkeypatch):
    dataset = tmp_path / "one.json"
    _written = dataset.write_text(json.dumps({"id": "one", "raw_transcript": "some words"}))
    calls = []
    real_client = httpx.Client
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, text="never reflect remote error contents")
        return httpx.Response(200, json={"choices": [{"message": {"content": "some words"}}]})
    monkeypatch.setattr("msj_bench.run.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert main(["llm", "--set", str(dataset), "--label", "failure", "--backend", "openai",
                 "--tasks", "cleanup,summarize,review", "--out-dir", str(tmp_path / "out")]) == 1
    directory, = (tmp_path / "out").iterdir()
    records = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines()]
    assert [r["status"] for r in records] == ["error", "ok", "error"]
    assert len(calls) == 2
    assert "remote error contents" not in (directory / "results.jsonl").read_text()


def test_config_and_output_validation(tmp_path):
    config = load_config(BENCH_ROOT / "config.toml")
    assert config.hourly_usd == .58 and config.stt_endpoint == "b92qs7e2v6wi3n"
    with pytest.raises(BenchmarkError, match="outside"):
        output_root(BENCH_ROOT / "generated")
    path = tmp_path / "bad.toml"
    _written = path.write_text('[runpod]\ntimeout_s = -1\n')
    with pytest.raises(BenchmarkError, match="timeout_s"):
        load_config(path)
