# Manual Review

## Expected Review Points

### Stage 05

Review:

- `outputs/deck_spec.json`
- `outputs/slide_titles.md`
- `outputs/slide_content.md`
- `outputs/speaker_notes.md`
- `outputs/narration_script.md`
- `outputs/notebooklm_request.json`
- `outputs/notebooklm_response.json`

If NotebookLM failed, read `outputs/manual_recovery.md` and continue by editing the fallback artifacts.

### Stage 06

Edit files in:

- `edits/approved_deck_spec.json`
- `edits/approved_narration_script.md`
- `edits/slide_titles.md`
- `edits/slide_content.md`
- `edits/speaker_notes.md`

Rerun stage 06 after edits to refresh the approved outputs and diff report.

### Stage 08

Review:

- `outputs/text_audio_map.json`
- `outputs/per_slide_audio/`
- `outputs/alignment.json`
- `outputs/subtitles_regenerated.srt`

If the TTS adapter falls back, replace clips or rerun with a working provider.

### Stage 09

Replace or patch clips in:

- `edits/approved_per_slide_audio/`
- `edits/approved_text_audio_map.json`

Rerun stage 09 to recompute merged narration and subtitle timing.

## Downstream Preference

Downstream stages prefer matching files under `edits/` over `outputs/`. If an edited artifact changes, downstream stages are marked `stale`.
