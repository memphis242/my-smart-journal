# Phase 0 - RunPod baseline

## Endpoints (Serverless, scale-to-zero, max 1 worker, FlashBoot on)

| Name | Endpoint ID | Worker (Hub release) | GPU pools | Model |
|---|---|---|---|---|
| msj-bench-whisper | b92qs7e2v6wi3n | runpod-workers/worker-faster_whisper 1.0.10 | AMPERE_16, AMPERE_24, ADA_24; min CUDA 12.3, no upper cap | Whisper large-v3-turbo (`"model": "turbo"`; the worker reports `"turbo"`) |
| msj-bench-llm | rcybvy10rvnu2q | eniewold/llama-cpp-runpod b11723 | AMPERE_24, ADA_24, ADA_32_PRO; min CUDA 12.8 | unsloth/gemma-4-26B-A4B-it-GGUF UD-Q4_K_XL, ctx 16384 |

Serverless prices (list-gpu-types, 2026-11-10): AMPERE_16 $0.58/hr, AMPERE_24 $0.75/hr, ADA_24 $1.34/hr, ADA_32_PRO $1.79/hr.

Both endpoints are pinned to EU-RO-1 by the network volume. RunPod support (2026-11-08) traced intermittent
`ENDPOINT_PAUSED` responses to GPU pools with no stock there, so keep several pools that EU-RO-1 stocks
(check `get-data-center EU-RO-1` with GPU availability). EU-RO-1 hosts report CUDA 13.2, so do not cap
`allowedCudaVersions` below that; faster-whisper 1.0.10 runs on it (verified 2026-11-10).

## Request formats

STT: `{"input": {"audio": "<url>", "model": "turbo"}}`

LLM (OpenAI passthrough; `max_tokens` is ignored outside this form):

```json
{"input": {"openai_route": "/v1/chat/completions", "openai_input": {
  "model": "any", "max_tokens": 200,
  "extra_body": {"chat_template_kwargs": {"enable_thinking": false}},
  "messages": [{"role": "user", "content": "..."}]}}}
```

## Smoke results (2026-11-06)

- STT, jfk.wav (11 s) on a cold worker: queue delay 7.7 s, execution 31.7 s (includes model load); transcript exact.
- LLM: cold start ~2.5-3 min (the worker downloads the GGUF on every new worker); warm job 0.6 s;
  ~100 tok/s generation, ~820 tok/s prompt on a 24 GB-class GPU.
- Pitfall: a simple `{"messages": ...}` input ignored `max_tokens` and with thinking enabled generated
  13k+ tokens until cancelled.

## Follow-ups

- Enable RunPod model caching (`LLAMA_CACHED_MODEL`) or a network volume to cut LLM cold start.
- Swap LLM candidates via the `LLAMA_ARG_HF_REPO` / `LLAMA_HF_QUANT` env (new release per change).
- Longer term: our own worker image combining STT + LLM so one cold start serves a whole batch.

## Candidate shortlist (from 2026-11-06 web research; verify model cards)

- STT: whisper-large-v3-turbo (baseline, no newer open Whisper exists); Qwen3-ASR-1.7B or Parakeet-TDT-0.6B.
- LLM: LFM2.5-350M, LFM2.5-1.2B-Instruct, Granite-4.0-1B, Qwen3.5-2B, Gemma-4-E4B-it, Qwen3.5-4B, Phi-4-mini (control).
  GGUF repos confirmed on HF: unsloth/Qwen3.5-{2B,4B}-GGUF, unsloth/gemma-4-E4B-it-GGUF, LiquidAI/LFM2.5-1.2B-Instruct-GGUF.
- Embeddings (CPU on VPS): embeddinggemma-300m; bge-small-en-v1.5 as fast fallback.
