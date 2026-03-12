# yt2slides

`yt2slides` is a local-first, resumable Python pipeline that turns a YouTube video into a narrated slide video. Every run is stored on disk under `workspace/runs/{run_id}` and every stage writes its own `inputs/`, `outputs/`, `edits/`, `logs/`, `status.json`, and `README.md`.

The pipeline is stage-based, adapter-backed, and built so you can stop, inspect, edit, rerun, and resume without losing provenance.

## Requirements Covered

- `yt-dlp` is isolated behind [`src/yt2slides/source_acquisition/yt_dlp_adapter.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/source_acquisition/yt_dlp_adapter.py)
- `notebooklm-mcp-cli` is isolated behind [`src/yt2slides/notebooklm/notebooklm_mcp_cli_adapter.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/notebooklm/notebooklm_mcp_cli_adapter.py)
- FFmpeg media processing and composition live in [`src/yt2slides/rendering/video.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/rendering/video.py)
- TTS / voice cloning adapters live under [`src/yt2slides/tts`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/tts)
- Stage orchestration, stale invalidation, locking, and resumability live under [`src/yt2slides/pipeline`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/yt2slides/pipeline)

## Install

```bash
python -m pip install -e .[dev]
```

## Config

Copy the examples:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

Then set:

- `source.youtube_url` or use CLI overrides
- `tts.reference_audio_path`
- `tts.command_template` for your voice-cloning provider
- `notebooklm.cli_path` and `notebooklm.profile` if needed

Secrets and environment-specific paths can use `${ENV_VAR}` syntax in YAML.

## CLI

Create a run scaffold:

```bash
app new-run --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Start a full run:

```bash
app start --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID" --reference-audio /path/to/voice.wav
```

Use a local video:

```bash
app start --local-video /path/to/source.mp4 --reference-audio /path/to/voice.wav
```

Run one stage:

```bash
app run-stage --run-id <run-id> --stage 05_generate_deck_with_notebooklm
```

Resume:

```bash
app resume --run-id <run-id>
```

Rerun from a stage:

```bash
app rerun --run-id <run-id> --from-stage 07_render_slides
```

Inspect status:

```bash
app status --run-id <run-id>
app inspect --run-id <run-id> --stage 09_review_and_patch_audio
```

Export deliverables:

```bash
app export --run-id <run-id>
```

## Pipeline Stages

1. `00_init_run`
2. `01_download_source`
3. `02_extract_media`
4. `03_transcribe`
5. `04_structure_content`
6. `05_generate_deck_with_notebooklm`
7. `06_review_and_patch_deck`
8. `07_render_slides`
9. `08_generate_voice`
10. `09_review_and_patch_audio`
11. `10_compose_video`
12. `11_qa_report`
13. `12_export`

## Key Behavior

- Downstream stages prefer `edits/` artifacts when present.
- If an edited or upstream preferred artifact changes, downstream stages are marked `stale`.
- Stage locks prevent concurrent corruption.
- Raw requests, responses, logs, and manifests are preserved on disk.
- Voice generation requires an explicit reference sample and writes consent guidance.

## Project Layout

See [docs/pipeline.md](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/pipeline.md) for the full tree and stage contracts.

## Documentation

- [docs/pipeline.md](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/pipeline.md)
- [docs/manual-review.md](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/manual-review.md)
- [docs/providers.md](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/providers.md)
- [docs/resume-and-rerun.md](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/resume-and-rerun.md)
- [docs/schemas/run_manifest.schema.json](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/schemas/run_manifest.schema.json)
- [docs/schemas/stage_status.schema.json](/Users/tianyi/Documents/Zheng/Code/lang-trade/docs/schemas/stage_status.schema.json)
