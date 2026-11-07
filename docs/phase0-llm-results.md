# Phase 0 - LLM benchmark, synthetic set (2026-11-06)

12 fictional entries x 4 tasks (cleanup, summarize, extract, review) per model, RunPod Serverless
(llama.cpp worker, RTX A4500 class, $0.58/hr), temperature 0. All runs 48/48 OK. Flags re-scored with
the fixed faithfulness guard; remaining flags are reformatting (e.g. "2026-11-18" -> "18 November 2026",
"1.5 L" -> "1.5L"), not invented facts, on manual inspection.

| Model | Cleanup WER vs reference | Content preservation | Key-fact recall (cleanup / summary) | Flags/item (cleanup, summary, extract) | Summary length ratio | Extract latency s | $/entry, all 4 tasks |
|---|---|---|---|---|---|---|---|
| Qwen3.5-4B Q4_K_M | 0.107 | 0.93 | 0.75 / 0.86 | 0, 0, 0 | 0.92 | 4.8 | 0.0013 |
| LFM2.5-350M BF16 | 0.337 | 0.71 | 0.72 / 0.46 | 0, 0.33, 0 | 0.38 | 2.5 | 0.0006 |
| LFM2.5-1.2B Q8_0 | 0.335 | 0.76 | 0.76 / 0.49 | 0.42, 0.42, 0.17 | 0.54 | 2.9 | 0.0007 |
| Gemma-4-E2B Q8_0 | 0.077 | 0.94 | 0.77 / 0.88 | 0, 0, 0 | 0.81 | 5.0 | 0.0012 |
| Phi-4-mini Q8_0 | **0.034** | **0.98** | **0.97** / 0.74 | 0, 0.33, 0.17 | 0.90 | 8.4 | 0.0020 |
| Qwen3.5-4B Q8_0 | 0.089 | 0.94 | 0.78 / 0.86 | 0, 0, 0 | 0.92 | 6.5 | 0.0016 |

Cost excludes cold starts. Total RunPod serverless spend for the day (billing read): $0.28.

## Takeaways

- LFM2.5 (350M, 1.2B) drop too much content for cleanup; not viable as the main model.
- Phi-4-mini is the best cleanup model by a wide margin; Gemma-4-E2B and Qwen3.5-4B are the strongest
  summarizers (key-fact recall 0.86-0.88). 8-bit Qwen improves on 4-bit modestly.
- The summary prompt is too lenient: summaries are 80-92% of source length. Tighten before the next round.
- Next: human blind ratings (ratings-*.csv in each results dir), user recordings for STT, prompt tuning.

## 9. Summary prompt iterations (Gemma-4-E2B Q8_0, synthetic set, 12 items)

| Prompt | Style | Length vs source | Key-fact recall | Flags/entry |
|---|---|---|---|---|
| v1 (baseline) | short paragraph | 0.81 | 0.88 | 0 |
| v2 | 2-4 sentences, <= 1/3 length | 0.58 | 0.86 | 0 |
| v3 | ~1/4 length, dense | 0.58 | 0.85 | 0.08 |
| v4 | 3-5 terse bullets | 0.32 | 0.72 | 0.42 |
| **v5 (kept)** | ~half length, explicit must-keep list | 0.69 | **0.94** | 0 |

- The model does not follow numeric length targets; v2 and v3 land at the same length.
- Bullets compress hard but drop names, units and acronyms.
- v5's must-keep list (full names, acronyms, numbers with units, weekdays) raised recall. Some remaining misses are metric artifacts: "five" vs "5", and "12 cm" is the value the speaker corrected away from.
- Synthetic entries are ~115 words; real entries will be longer, so recheck length on the user's recordings.

## 10. Long entries and compilation (Gemma-4-E2B Q8_0)

Datasets: `bench/data/synthetic-long` (6 fictional dictated entries, 590-790 words) and `bench/data/compile` (3 fictional periods: monthly, quarterly, job-search; 8-10 cleaned entries each, with `not_accomplished` traps).

Summary prompt:

| Prompt | Long: length | Long: key facts | Short: length | Short: key facts | Flags |
|---|---|---|---|---|---|
| v5 (prose, must-keep list) | 0.36 | 0.84 | 0.69 | 0.94 | ~0 |
| **v6 (kept): Done / Open / Notes paragraphs** | 0.37 | 0.81 | 0.83 | 0.95 | 0 |

v6 keeps recall roughly level and separates completed work from plans, which compilation depends on; it runs long on very short entries.

Compile prompt (3 sets):

| Prompt | Key facts | Plan-as-accomplishment leaks | Valid evidence dates | Flags |
|---|---|---|---|---|
| v1 | 0.66 | 0.33 | 1.00 | 0.33 |
| v2 (truthfulness rules, exhaustive open_threads) | 0.77 | 0 | 0.67 | 3.0 |
| v2 + evidence_dates enum in schema | 0.81 | 0 | 1.00 | 2.67 |
| **v3 (kept): named people in STAR, sentence depth notes** | 0.79 | 0 | 1.00 | 0 |

- Constraining `evidence_dates` to an enum of the input dates (grammar decoding) removes malformed or invented dates entirely; the app must build this schema per request.
- v2 flags were title-case labels inside `depth_notes`, not invented facts.
- Remaining misses are mostly secondary people and investigation numbers; worth rechecking with the user's real entries and with blind ratings.
