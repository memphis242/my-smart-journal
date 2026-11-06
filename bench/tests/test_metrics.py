import json

import pytest

from msj_bench.backends import Generation
from msj_bench.metrics import faithfulness_guard, json_checks, llm_metrics, normalize, performance_metrics, stt_metrics
from msj_bench.tasks import extraction_schema


def test_normalization_unicode_numbers_versions():
    assert normalize("Zoë, CAFÉ! v2 uses 3.5 V.") == "zoë café v2 uses 3.5 v"
    assert stt_metrics("Zoë, café; 3.5 V v2!", "ZOË café 3.5 v V2") == {"wer": 0, "cer": 0}
    assert stt_metrics("3.5", "35")["wer"] == 1
    assert stt_metrics("v2", "v3")["wer"] == 1


@pytest.mark.parametrize(("reference", "output", "expected"), [("", "", 0), ("one two", "", 1), ("", "new words", 2)])
def test_empty_transcripts(reference, output, expected):
    assert stt_metrics(reference, output)["wer"] == expected


def test_guard_exact_numbers_dates_names():
    source = "Zoë ran v2 at 3.5 V on Wednesday, 2026-11-18, with STM32."
    assert faithfulness_guard(source, source)["hallucination_flag_count"] == 0
    flags = faithfulness_guard(source, "Zoë ran v3 at 3.7 V on Thursday with Niko.")
    assert set(flags["hallucination_flags"]) == {"v3", "3.7", "Thursday", "Niko"}
    assert "2" in faithfulness_guard("12 trials", "2 trials")["unsupported_numbers_dates"]
    assert "2026-11-19" in faithfulness_guard(source, "2026-11-19")["unsupported_numbers_dates"]
    assert "thursday" in faithfulness_guard(source, "on thursday")["unsupported_numbers_dates"]
    assert faithfulness_guard("3.5 V", "３.５ V")["hallucination_flag_count"] == 0


def test_cleanup_realistic_and_reference():
    source = "Um, I, I measured the sensor at 3.5 V with Mira."
    cleaned = "I measured the sensor at 3.5 V with Mira."
    metrics = llm_metrics("cleanup", source, cleaned, cleaned, ("Mira", "3.5 V"))
    assert metrics["content_preservation_recall"] == 1
    assert metrics["reference_wer"] == 0
    assert metrics["key_fact_recall"] == 1
    empty = llm_metrics("cleanup", source, "", cleaned)
    assert empty["content_preservation_recall"] == 0
    assert empty["reference_wer"] == 1
    assert llm_metrics("cleanup", "um uh the", "")["content_preservation_recall"] == 1


def test_json_validation_and_nested_grounding(empty_extraction):
    value = {**empty_extraction,
             "projects": ["Lantern"],
             "accomplishments": [{"description": "Reduced latency", "metrics": ["20 ms"]}],
             "people_roles": [{"name": "Mira", "role": "engineer"}],
             "star_fragments": [{"situation": "slow sensor", "task": "", "action": "Reduced latency", "result": "20 ms"}]}
    source = "Mira, the engineer on Lantern, reduced latency to 20 ms for a slow sensor."
    valid = llm_metrics("extract", source, json.dumps(value))
    assert valid["json_parses"] and valid["schema_valid"] and valid["all_strings_grounded"]
    value["people_roles"][0]["name"] = "Invented Person"
    invalid = llm_metrics("extract", source, json.dumps(value))
    assert invalid["schema_valid"] and not invalid["all_strings_grounded"]
    assert invalid["ungrounded_fields"][0]["path"] == "$.people_roles[0].name"
    assert not llm_metrics("extract", source, "{}")["schema_valid"]
    assert not llm_metrics("extract", source, "```json\n{}\n```")["json_parses"]
    assert not json_checks('NaN', extraction_schema())["json_parses"]


def test_summary_review_and_performance():
    assert llm_metrics("summarize", "one two three four", "one two")["compression_ratio"] == .5
    assert llm_metrics("summarize", "", "")["compression_ratio"] is None
    assert llm_metrics("review", "a source", '{"unsupported_claims": ["invented metric"]}')["unsupported_claim_count"] == 1
    result = performance_metrics(Generation("hi", "reported-model", 5, 20, 100, 2000, 3000, "worker"), .58)
    assert result["tokens_per_s"] == pytest.approx(100 / 3)
    assert result["estimated_cost_usd"] == pytest.approx(5 * .58 / 3600)
    assert performance_metrics(Generation("", None, 0), .58)["tokens_per_s"] is None
    assert performance_metrics(Generation("local", "cpu", 2, completion_tokens=10), None)["estimated_cost_usd"] is None


def test_guard_ignores_possessives_and_sentence_initial_words() -> None:
    from msj_bench.metrics import faithfulness_guard
    source = "Jules Reed and I opened ticket HBR-42."
    output = "Consequently, Jules's ticket HBR-42 is open and Morgan approved it. Taylor agreed."
    names = faithfulness_guard(source, output)["unsupported_names"]
    # Known limitation: a new name that only appears sentence-initially ("Taylor") is not flagged.
    assert names == ["Morgan"]


def test_guard_number_formatting_and_words() -> None:
    from msj_bench.metrics import faithfulness_guard
    source = "At 115200 baud we saw 2 errors in 10000 bytes. Nineteen reconnects passed."
    output = "At 115,200 baud: 2 errors in 10,000 bytes; 19 reconnects passed, 21 total."
    assert faithfulness_guard(source, output)["unsupported_numbers_dates"] == ["21"]


def test_guard_ignores_json_field_initial_words_and_contractions() -> None:
    from msj_bench.metrics import faithfulness_guard
    source = "i will review the ring buffer and i am testing it"
    output = '{"actions": ["Reviewed ring buffer", "Testing it"], "note": "I’ll test; I’m done"}'
    assert faithfulness_guard(source, output)["unsupported_names"] == []
