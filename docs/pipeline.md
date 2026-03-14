# Pipeline

## Overview

The application is a file-backed multi-stage pipeline. Each run lives under:

```text
workspace/
  runs/
    {run_id}/
      run_manifest.json
      config.snapshot.json
      00_init_run/
      01_download_source/
      02_extract_media/
      03_transcribe/
      04_structure_content/
      05_generate_deck_with_notebooklm/
      06_review_and_patch_deck/
      07_render_slides/
      08_generate_voice/
      09_review_and_patch_audio/
      10_compose_video/
      11_qa_report/
      12_export/
```

Each stage directory contains:

```text
inputs/
outputs/
edits/
logs/
status.json
README.md
```

## Source Tree

```text
src/
  cli.py
  config.py
  exceptions.py
  structured_logging.py
  utils.py
  pipeline/
    base.py
    orchestrator.py
    workspace.py
  source_acquisition/
    base.py
    local_media_adapter.py
    yt_dlp_adapter.py
  transcription/
    base.py
    command_provider.py
    whisper_cli.py
  notebooklm/
    base.py
    notebooklm_mcp_cli_adapter.py
  tts/
    base.py
    command_provider.py
    manual_provider.py
  rendering/
    slides.py
    video.py
  stages/
    pipeline_stages.py
```

## Stage Contracts

### 00_init_run

- Creates the run scaffold.
- Copies `run_manifest.json` and `config.snapshot.json` into stage outputs.

### 01_download_source

- Uses `yt-dlp` or a local media adapter.
- Writes `original_video.*`, `metadata.json`, `yt_dlp_command.txt`, `download_request.json`, and `download_logs.txt`.

### 02_extract_media

- Uses FFmpeg to extract `source_audio.wav`, `source_audio.mp3`, `source_audio_preview.mp3`, `media_info.json`, and `thumbnails/`.

### 03_transcribe

- Uses the configured transcription adapter.
- Writes `transcript_raw.json`, `transcript_clean.md`, `transcript_segments.json`, `subtitles.vtt`, and `subtitles.srt`.

### 04_structure_content

- Builds deterministic `outline.json`, `outline.md`, `key_points.json`, `glossary.json`, and `open_questions.md`.

### 05_generate_deck_with_notebooklm

- Calls `notebooklm-mcp-cli` only through the dedicated adapter.
- Writes raw request/response payloads plus normalized deck artifacts.

### 06_review_and_patch_deck

- Seeds editable review files into `edits/`.
- Produces `approved_deck_spec.json`, `approved_narration_script.md`, and `diff_report.md`.

### 07_render_slides

- Produces `deck.pptx`, `slide_images/`, `thumbnails/`, and `slide_timing_hints.json`.

### 08_generate_voice

- Requires an explicit reference audio sample.
- Produces `per_slide_audio/`, `narration_merged.wav`, `narration_merged.mp3`, `alignment.json`, `subtitles_regenerated.srt`, and text/audio mapping artifacts.

### 09_review_and_patch_audio

- Lets the user replace clips under `edits/approved_per_slide_audio/`.
- Recomputes merged narration and subtitle timing.

### 10_compose_video

- Uses FFmpeg to create `preview.mp4`, `final.mp4`, `chapters.json`, and `muxed_subtitles.vtt`.

### 11_qa_report

- Generates QA checks for transcript/narration overlap, missing slides, duration outliers, silent/clipped audio, missing media, and failed renders.

### 12_export

- Creates a final deliverables folder and provenance manifest.
