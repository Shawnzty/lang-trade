# Resume And Rerun

## New Run

```bash
app new-run --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

This creates the run directory and executes stage `00_init_run`.

## Start End-To-End

```bash
app start --youtube-url "https://www.youtube.com/watch?v=VIDEO_ID" --reference-audio /path/to/voice.wav
```

## Resume

```bash
app resume --run-id <run-id>
```

This runs any stage that is not `completed`.

## Run One Stage

```bash
app run-stage --run-id <run-id> --stage 08_generate_voice
```

## Rerun From One Stage

```bash
app rerun --run-id <run-id> --from-stage 06_review_and_patch_deck
```

This forces the selected stage and all later stages in the chosen range to rerun.

## Inspect

```bash
app inspect --run-id <run-id> --stage 10_compose_video
```

## Export

```bash
app export --run-id <run-id>
```

## Stale Detection

If a preferred upstream artifact changes, downstream stages are marked `stale`. The most common cause is editing files under `edits/`.
