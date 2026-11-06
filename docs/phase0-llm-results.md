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
