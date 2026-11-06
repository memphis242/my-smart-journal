# my-smart-journal

A private journal (work, school, life) with speech-to-text, LLM cleanup/summarization,
structured extraction, tiered search, and periodic compilation into resume and
interview-prep material. Inference runs on self-controlled hardware: local CPU or
on-demand rented GPUs (RunPod Serverless).

## Status

Phase 0 - benchmarking speech-to-text and small LLMs on local CPU vs. RunPod.

## Phases

0. Benchmark (0a: RunPod setup, 0b: model benchmark)
1. Core app (entries, job queue, cleanup + review, search tiers 1-4)
2. PWA with offline capture, RunPod provider
3. Semantic search, extraction, rollups, compile output
4. VPS deployment, restic backups
5. Text-to-speech
