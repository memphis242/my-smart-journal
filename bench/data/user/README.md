Place private recordings here as `<id>.wav`, `<id>.m4a`, `<id>.mp3`, or
`<id>.ogg`, with `<id>.txt` containing the verbatim reference transcript.
Use one recording per unique ID. The reference should retain spoken fillers,
false starts, and corrections. An optional `<id>.cleaned.txt` supplies a cleaned
reference for LLM cleanup evaluation and a candidate for review-only runs.

This folder's contents except this README are gitignored. Use `--set user` or
`--set /absolute/path/to/recordings`. Audio is sent as base64 to RunPod only
when an STT benchmark is explicitly run. LLM runs use the reference transcripts
and never send the audio. Avoid including personal material in committed fixtures.

A custom JSON object (or array of objects) can instead contain `id`,
`raw_transcript`, optional `reference_cleaned`, optional `key_facts` (strings),
and `audio` (a path relative to that JSON file, or an HTTP(S) audio URL).
A dataset directory can contain multiple JSON files. Do not also duplicate
an item's recording under another ID. Missing references, duplicate IDs, and
STT items without audio are errors before any benchmark request is sent.
