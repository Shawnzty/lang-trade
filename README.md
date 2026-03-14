# lang-trade

`lang-trade` is a local-first, resumable Python pipeline that turns a YouTube video into a narrated slide video. Every run is stored on disk under `workspace/runs/{run_id}` and every stage writes its own `inputs/`, `outputs/`, `edits/`, `logs/`, `status.json`, and `README.md`.

The pipeline is stage-based, adapter-backed, and built so you can stop, inspect, edit, rerun, and resume without losing provenance.

## Requirements Covered

- `yt-dlp` is isolated behind [`src/source_acquisition/yt_dlp_adapter.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/source_acquisition/yt_dlp_adapter.py)
- `notebooklm-mcp-cli` is isolated behind [`src/notebooklm/notebooklm_mcp_cli_adapter.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/notebooklm/notebooklm_mcp_cli_adapter.py)
- FFmpeg media processing and composition live in [`src/rendering/video.py`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/rendering/video.py)
- TTS / voice cloning adapters live under [`src/tts`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/tts)
- Stage orchestration, stale invalidation, locking, and resumability live under [`src/pipeline`](/Users/tianyi/Documents/Zheng/Code/lang-trade/src/pipeline)

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
- `tts.provider` to `fish_audio`, `elevenlabs`, `command`, or `manual`
- `tts.reference_audio_path`
- `tts.fish_audio.api_key` or `tts.elevenlabs.api_key` when using the built-in API providers
- `tts.command_template` if you prefer the generic command-based provider
- `tts.voice_id` if you want to reuse an existing cloned voice instead of recloning every run
- `notebooklm.cli_path` and `notebooklm.profile` if needed

Example TTS setup:

```yaml
tts:
  provider: fish_audio
  clone_voice_name: "My Narration Voice"
  reference_audio_path: "${VOICE_REFERENCE_AUDIO}"
  fish_audio:
    api_key: "${FISH_AUDIO_API_KEY}"
```

```yaml
tts:
  provider: elevenlabs
  clone_voice_name: "My Narration Voice"
  reference_audio_path: "${VOICE_REFERENCE_AUDIO}"
  elevenlabs:
    api_key: "${ELEVENLABS_API_KEY}"
    output_format: pcm_24000
```

Secrets and environment-specific paths can use `${ENV_VAR}` syntax in YAML.

## CLI

Create a run scaffold:

```bash
lang-trade new-run --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Start a full run:

```bash
lang-trade start --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID" --reference-audio /path/to/voice.wav
```

Use a local video:

```bash
lang-trade start --local-video /path/to/source.mp4 --reference-audio /path/to/voice.wav
```

Run one stage:

```bash
lang-trade run-stage --run-id <run-id> --stage 05_generate_deck_with_notebooklm
```

Resume:

```bash
lang-trade resume --run-id <run-id>
```

Rerun from a stage:

```bash
lang-trade rerun --run-id <run-id> --from-stage 07_render_slides
```

Inspect status:

```bash
lang-trade status --run-id <run-id>
lang-trade inspect --run-id <run-id> --stage 09_review_and_patch_audio
```

Export deliverables:

```bash
lang-trade export --run-id <run-id>
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
