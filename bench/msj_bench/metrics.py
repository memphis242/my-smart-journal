"""Deterministic lexical diagnostics, not a semantic truth oracle."""

from collections.abc import Iterator, Mapping
import json
import re
import unicodedata
from typing import Any, Final, Never

import jiwer
from jsonschema import Draft202012Validator

from .backends import Generation
from .tasks import extraction_schema, REVIEW_SCHEMA

STOPWORDS: Final = frozenset("""a an the and or but if then than so as at by for from in into of on onto to up with
i me my mine we us our ours you your yours he him his she her hers it its they them their theirs
this that these those am is are was were be been being do does did have has had will would
can could shall should may might must not no yes very just really also about again all any
each both some such there here what which who whom when where why how more most much many
one own same other only out over under through too well im ive id dont didnt isnt wasnt
um uh er ah hmm like actually basically literally okay ok kind sort mean know wait
""".split())
WORD_RE: Final = re.compile(r"[^\W_]+(?:[.'’/-][^\W_]+)*", re.UNICODE)
NUMBER_RE: Final = re.compile(r"(?<!\w)[+-]?\d+(?:[.,:/-]\d+)*(?:%|°[CF])?", re.UNICODE)
VERSION_RE: Final = re.compile(r"\b[vV]\d+(?:\.\d+)*\b")
DATE_WORD_RE: Final = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    chars: list[str] = []
    for index, char in enumerate(text):
        if char in {"'", "’"}:
            continue
        decimal = char == "." and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit()
        chars.append(char if decimal or unicodedata.category(char)[0] in {"L", "N", "M"} else " ")
    return " ".join("".join(chars).split())


def stt_metrics(reference: str, output: str) -> dict[str, float]:
    source, candidate = normalize(reference), normalize(output)
    # jiwer 4 defines insertion counts for an empty reference, including empty/empty.
    return {"wer": float(jiwer.wer(source, candidate)), "cer": float(jiwer.cer(source, candidate))}


def content_terms(text: str) -> frozenset[str]:
    return frozenset(word for word in normalize(text).split() if word not in STOPWORDS)


POSSESSIVE_RE: Final = re.compile(r"(?:'|\u2019)s$", re.IGNORECASE)
SENTENCE_START_RE: Final = re.compile(r"(?:^|[.!?:;]\s+|\n\s*)$")


def _unsupported_names(output: str, source_words: set[str]) -> set[str]:
    """Capitalized tokens absent from the source, ignoring possessives and ordinary sentence-initial words."""
    found: set[str] = set()
    for match in WORD_RE.finditer(output):
        token = POSSESSIVE_RE.sub("", match.group())
        if not token or token.isnumeric() or not any(c.isupper() for c in token):
            continue
        folded = token.casefold()
        if folded in source_words or folded in STOPWORDS:
            continue
        sentence_initial = SENTENCE_START_RE.search(output[: match.start()]) is not None
        if sentence_initial and token[1:].islower():
            continue
        found.add(token)
    return found


def faithfulness_guard(source: str, output: str) -> dict[str, Any]:
    source = unicodedata.normalize("NFKC", source)
    output = unicodedata.normalize("NFKC", output)
    source_numbers = {m.group().casefold() for m in NUMBER_RE.finditer(source)}
    source_versions = {m.group().casefold() for m in VERSION_RE.finditer(source)}
    source_dates = {m.group().casefold() for m in DATE_WORD_RE.finditer(source)}
    source_words = {token.casefold() for token in WORD_RE.findall(source)}
    numbers = sorted({m.group() for m in NUMBER_RE.finditer(output) if m.group().casefold() not in source_numbers})
    dates = {m.group() for m in DATE_WORD_RE.finditer(output) if m.group().casefold() not in source_dates}
    versions = sorted({m.group() for m in VERSION_RE.finditer(output) if m.group().casefold() not in source_versions})
    names = sorted(_unsupported_names(output, source_words))
    numbers_dates = sorted(set(numbers) | dates)
    flags = sorted(set(numbers_dates + versions + names))
    return {"unsupported_numbers_dates": numbers_dates, "unsupported_versions": versions,
            "unsupported_names": names, "hallucination_flags": flags, "hallucination_flag_count": len(flags)}


def string_fields(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, entry in enumerate(value):
            yield from string_fields(entry, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, entry in value.items():
            yield from string_fields(entry, f"{path}.{key}")


def json_checks(output: str, schema: Mapping[str, Any], source: str | None = None) -> dict[str, Any]:
    def reject_constant(_value: str) -> Never:
        raise ValueError("Non-finite constants are not valid JSON")

    try:
        value = json.loads(output, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        return {"json_parses": False, "schema_valid": False, "schema_errors": ["Output is not valid JSON"],
                "ungrounded_fields": [], "all_strings_grounded": None}
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    # Report paths and constraint names, avoiding giant dumps of an invalid response.
    details = [f"{'.'.join(map(str, e.path)) or '$'}: {e.validator}" for e in errors]
    ungrounded: list[dict[str, Any]] = []
    if source is not None:
        terms = set(normalize(source).split())
        for path, field in string_fields(value):
            missing = sorted(content_terms(field) - terms)
            if missing:
                ungrounded.append({"path": path, "missing_terms": missing})
    return {"json_parses": True, "schema_valid": not errors, "schema_errors": details,
            "ungrounded_fields": ungrounded,
            "all_strings_grounded": not ungrounded if source is not None else None}


def llm_metrics(task: str, source: str, output: str, reference: str | None = None,
                key_facts: tuple[str, ...] = ()) -> dict[str, Any]:
    result = faithfulness_guard(source, output)
    if key_facts:
        output_terms = set(normalize(output).split())
        missing_facts = [fact for fact in key_facts if not set(normalize(fact).split()) <= output_terms]
        result.update(missing_key_facts=missing_facts, key_fact_recall=1 - len(missing_facts) / len(key_facts))
    match task:
        case "cleanup":
            terms = content_terms(source)
            preserved = terms & content_terms(output)
            recall = len(preserved) / len(terms) if terms else 1.0
            assert 0 <= recall <= 1
            result["content_preservation_recall"] = recall
            if reference is not None:
                result["reference_wer"] = stt_metrics(reference, output)["wer"]
        case "summarize":
            source_length = len(normalize(source).split())
            result["compression_ratio"] = len(normalize(output).split()) / source_length if source_length else None
        case "extract":
            result.update(json_checks(output, extraction_schema(), source))
        case "review":
            result.update(json_checks(output, REVIEW_SCHEMA))
            if result["schema_valid"]:
                result["unsupported_claim_count"] = len(json.loads(output)["unsupported_claims"])
    return result


def performance_metrics(generation: Generation, hourly_usd: float | None) -> dict[str, Any]:
    execution_s = generation.execution_time_ms / 1000 if generation.execution_time_ms is not None else None
    delay_s = generation.delay_time_ms / 1000 if generation.delay_time_ms is not None else None
    denominator = execution_s if execution_s is not None else generation.wall_time_s
    tokens_per_s = generation.completion_tokens / denominator if generation.completion_tokens is not None and denominator > 0 else None
    cost = (execution_s + delay_s) * hourly_usd / 3600 if execution_s is not None and delay_s is not None and hourly_usd is not None else None
    return {"wall_time_s": generation.wall_time_s, "delayTime": generation.delay_time_ms,
            "executionTime": generation.execution_time_ms, "worker_id": generation.worker_id,
            "job_id": generation.job_id, "prompt_tokens": generation.prompt_tokens,
            "completion_tokens": generation.completion_tokens, "tokens_per_s": tokens_per_s,
            "estimated_cost_usd": cost, "finish_reason": generation.finish_reason,
            "truncated": generation.finish_reason == "length"}
