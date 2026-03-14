"""Stage implementations for the narrated slide pipeline."""

from __future__ import annotations

import difflib
import json
import math
import re
import shutil
import statistics
import wave
from collections import Counter
from pathlib import Path
from typing import Any

from notebooklm import NotebookLMAdapterError, NotebookLMMcpCliAdapter
from pipeline.base import ArtifactRecord, BaseStage, StageContext, StageDefinition, StageResult
from rendering import FFmpegVideoRenderer, SlideRenderer
from source_acquisition import LocalMediaAdapter, YtDlpAdapter
from transcription import CommandTranscriptionAdapter, WhisperCliTranscriptionAdapter
from tts import CommandTTSAdapter, ElevenLabsTTSAdapter, FishAudioTTSAdapter, ManualTTSAdapter, NarrationSegment
from utils import (
    atomic_write_json,
    atomic_write_text,
    chunked,
    copy_file,
    copy_tree,
    ensure_dir,
    estimate_seconds_from_text,
    hash_file,
    now_utc,
    read_json,
    read_text,
    wrap_text,
)


STOPWORDS = {
    "about",
    "also",
    "and",
    "are",
    "been",
    "because",
    "could",
    "from",
    "have",
    "into",
    "more",
    "should",
    "that",
    "their",
    "there",
    "they",
    "this",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "will",
    "with",
    "would",
    "your",
}


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _source_adapter(config: dict[str, Any]) -> Any:
    source_config = config["source"]
    if source_config.get("local_video"):
        return LocalMediaAdapter(source_config["local_video"])
    yt_dlp_config = config["yt_dlp"]
    return YtDlpAdapter(
        format_selector=yt_dlp_config["format"],
        merge_output_format=yt_dlp_config["merge_output_format"],
        output_template=yt_dlp_config["output_template"],
        cookies_from_browser=yt_dlp_config.get("cookies_from_browser", ""),
        write_info_json=bool(yt_dlp_config.get("write_info_json", True)),
    )


def _transcription_adapter(config: dict[str, Any]) -> Any:
    transcription = config["transcription"]
    provider = transcription.get("provider", "whisper_cli")
    if provider == "whisper_cli":
        return WhisperCliTranscriptionAdapter(
            model=transcription.get("whisper_model", "turbo"),
            language=transcription.get("language", ""),
            extra_args=list(transcription.get("extra_args", [])),
        )
    if provider == "command":
        return CommandTranscriptionAdapter(transcription.get("command_template", ""))
    raise ValueError(f"Unsupported transcription provider: {provider}")


def _notebooklm_adapter(config: dict[str, Any]) -> NotebookLMMcpCliAdapter:
    notebooklm = config["notebooklm"]
    provider = notebooklm.get("provider", "notebooklm_mcp_cli")
    if provider != "notebooklm_mcp_cli":
        raise ValueError(f"Unsupported NotebookLM provider: {provider}")
    return NotebookLMMcpCliAdapter(
        cli_path=notebooklm.get("cli_path", "nlm"),
        profile=notebooklm.get("profile", ""),
        retries=int(notebooklm.get("retries", 3)),
        query_timeout_seconds=int(notebooklm.get("query_timeout_seconds", 180)),
    )


def _tts_adapter(config: dict[str, Any]) -> Any:
    tts = config["tts"]
    provider = tts.get("provider", "command")
    if provider == "command":
        return CommandTTSAdapter(
            command_template=tts.get("command_template", ""),
            ffprobe_path=config["ffmpeg"].get("ffprobe_path", "ffprobe"),
        )
    if provider == "fish_audio":
        fish_audio = tts.get("fish_audio", {})
        return FishAudioTTSAdapter(
            api_key=str(fish_audio.get("api_key", "")),
            sample_rate=int(tts.get("sample_rate", 24000)),
            model=str(fish_audio.get("model", "s1")),
            clone_name=str(tts.get("clone_voice_name", "")),
            visibility=str(fish_audio.get("visibility", "private")),
            train_mode=str(fish_audio.get("train_mode", "fast")),
            enhance_audio_quality=_as_bool(fish_audio.get("enhance_audio_quality", True), default=True),
            latency=str(fish_audio.get("latency", "normal")),
            normalize=_as_bool(fish_audio.get("normalize", True), default=True),
            reference_text=str(fish_audio.get("reference_text", "")),
            timeout_seconds=int(fish_audio.get("timeout_seconds", 120)),
            clone_poll_attempts=int(fish_audio.get("clone_poll_attempts", 1)),
            clone_poll_interval_seconds=float(fish_audio.get("clone_poll_interval_seconds", 2.0)),
            ffprobe_path=config["ffmpeg"].get("ffprobe_path", "ffprobe"),
        )
    if provider == "elevenlabs":
        elevenlabs = tts.get("elevenlabs", {})
        return ElevenLabsTTSAdapter(
            api_key=str(elevenlabs.get("api_key", "")),
            sample_rate=int(tts.get("sample_rate", 24000)),
            model_id=str(elevenlabs.get("model_id", "eleven_multilingual_v2")),
            output_format=str(elevenlabs.get("output_format", "pcm_24000")),
            clone_name=str(tts.get("clone_voice_name", "")),
            remove_background_noise=_as_bool(elevenlabs.get("remove_background_noise", False), default=False),
            description=str(elevenlabs.get("description", "")),
            labels=dict(elevenlabs.get("labels", {})),
            language_code=str(elevenlabs.get("language_code", "")),
            voice_settings=dict(elevenlabs.get("voice_settings", {})),
            enable_logging=_as_bool(elevenlabs.get("enable_logging", True), default=True),
            timeout_seconds=int(elevenlabs.get("timeout_seconds", 120)),
            ffprobe_path=config["ffmpeg"].get("ffprobe_path", "ffprobe"),
        )
    if provider == "manual":
        return ManualTTSAdapter()
    raise ValueError(f"Unsupported TTS provider: {provider}")


def _video_renderer(config: dict[str, Any]) -> FFmpegVideoRenderer:
    ffmpeg = config["ffmpeg"]
    return FFmpegVideoRenderer(
        ffmpeg_path=ffmpeg.get("ffmpeg_path", "ffmpeg"),
        ffprobe_path=ffmpeg.get("ffprobe_path", "ffprobe"),
    )


def _slide_renderer(config: dict[str, Any]) -> SlideRenderer:
    return SlideRenderer(config["slide_renderer"])


def _copy_if_missing(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        copy_tree(source, destination)
    else:
        copy_file(source, destination)


def _markdown_deck_parts(deck_spec: dict[str, Any]) -> tuple[str, str, str, str]:
    titles = ["# Slide Titles", ""]
    content = ["# Slide Content", ""]
    notes = ["# Speaker Notes", ""]
    narration = ["# Narration Script", ""]
    for slide in deck_spec.get("slides", []):
        number = int(slide["slide_number"])
        title = str(slide.get("title", f"Slide {number}"))
        titles.append(f"- {number}. {title}")
        content.extend(
            [
                f"## Slide {number}: {title}",
                "",
                *[f"- {bullet}" for bullet in slide.get("bullets", [])],
                "",
            ]
        )
        notes.extend(
            [
                f"## Slide {number}: {title}",
                "",
                wrap_text(str(slide.get("speaker_notes", ""))),
                "",
            ]
        )
        narration.extend(
            [
                f"## Slide {number}: {title}",
                "",
                wrap_text(str(slide.get("narration_text", ""))),
                "",
            ]
        )
    return (
        "\n".join(titles).rstrip() + "\n",
        "\n".join(content).rstrip() + "\n",
        "\n".join(notes).rstrip() + "\n",
        "\n".join(narration).rstrip() + "\n",
    )


def _render_diff(before_text: str, after_text: str, *, before_name: str, after_name: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=before_name,
            tofile=after_name,
            lineterm="",
        )
    )


def _write_srt(entries: list[dict[str, Any]], path: Path) -> None:
    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        start = _format_srt_timestamp(float(entry["start_sec"]))
        end = _format_srt_timestamp(float(entry["end_sec"]))
        lines.extend([str(index), f"{start} --> {end}", str(entry["text"]).strip(), ""])
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def _write_vtt(entries: list[dict[str, Any]], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for entry in entries:
        start = _format_vtt_timestamp(float(entry["start_sec"]))
        end = _format_vtt_timestamp(float(entry["end_sec"]))
        lines.extend([f"{start} --> {end}", str(entry["text"]).strip(), ""])
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
    return _format_srt_timestamp(seconds).replace(",", ".")


def _plain_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9'\-]+", text.lower())


def _build_outline(clean_markdown: str, transcript_segments: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    content_lines = [
        line.strip()
        for line in clean_markdown.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    paragraphs = [line for line in content_lines if line]
    duration_minutes = max(1.0, (float(transcript_segments[-1]["end"]) if transcript_segments else 300.0) / 60.0)
    target_slides = max(
        4,
        min(
            int(config["structure"].get("max_slides", 16)),
            math.ceil(duration_minutes * float(config["structure"].get("target_slides_per_minute", 1.4))),
        ),
    )
    grouped = chunked(paragraphs, max(1, math.ceil(max(len(paragraphs), 1) / target_slides)))
    candidate_slides: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    for index, chunk in enumerate(grouped, start=1):
        summary = chunk[0][:120] if chunk else f"Section {index}"
        title = re.split(r"[.!?]", summary)[0][:80] or f"Slide {index}"
        bullets = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", " ".join(chunk)) if sentence.strip()][:4]
        candidate_slides.append(
            {
                "slide_number": index,
                "title": title,
                "objective": summary,
                "bullets": bullets,
            }
        )
        if index == 1 or (index - 1) % 3 == 0:
            chapters.append(
                {
                    "chapter_number": len(chapters) + 1,
                    "title": title,
                    "start_slide": index,
                    "summary": summary,
                }
            )
    words = [word for word in _plain_words(clean_markdown) if word not in STOPWORDS and len(word) > 4]
    glossary = [
        {"term": term, "count": count}
        for term, count in Counter(words).most_common(int(config["structure"].get("glossary_size", 12)))
    ]
    key_points = [
        {"rank": index, "text": slide["objective"]}
        for index, slide in enumerate(candidate_slides[:10], start=1)
    ]
    return {
        "title": candidate_slides[0]["title"] if candidate_slides else "Generated Deck",
        "chapters": chapters,
        "candidate_slide_boundaries": candidate_slides,
        "key_points": key_points,
        "glossary": glossary,
    }


def _fallback_deck_spec(outline: dict[str, Any]) -> dict[str, Any]:
    slides: list[dict[str, Any]] = []
    for candidate in outline.get("candidate_slide_boundaries", []):
        slides.append(
            {
                "slide_number": int(candidate["slide_number"]),
                "title": str(candidate["title"]),
                "objective": str(candidate.get("objective", "")),
                "on_slide_text": str(candidate.get("objective", "")),
                "bullets": list(candidate.get("bullets", [])),
                "suggested_visual": f"Use a clean visual that supports {candidate['title']}.",
                "speaker_notes": f"Explain {candidate['title']} with examples from the transcript.",
                "narration_text": " ".join(candidate.get("bullets", [])) or str(candidate.get("objective", "")),
                "estimated_duration_sec": estimate_seconds_from_text(" ".join(candidate.get("bullets", []))),
            }
        )
    return {
        "deck_title": outline.get("title", "Fallback Deck"),
        "deck_summary": "Fallback deck generated from the deterministic outline.",
        "slides": slides,
        "asset_requests": [
            {
                "slide_number": slide["slide_number"],
                "request": slide["suggested_visual"],
                "priority": "medium",
            }
            for slide in slides
        ],
    }


def _parse_narration_script(script_text: str, deck_spec: dict[str, Any]) -> list[NarrationSegment]:
    segments_by_slide: dict[int, str] = {}
    matches = re.findall(
        r"##\s+Slide\s+(\d+):.*?\n(.*?)(?=\n##\s+Slide\s+\d+:|\Z)",
        script_text,
        flags=re.DOTALL,
    )
    for slide_number, block in matches:
        segments_by_slide[int(slide_number)] = " ".join(line.strip() for line in block.splitlines() if line.strip())
    segments: list[NarrationSegment] = []
    for slide in deck_spec.get("slides", []):
        slide_number = int(slide["slide_number"])
        text = segments_by_slide.get(slide_number) or str(slide.get("narration_text", ""))
        segments.append(NarrationSegment(slide_number=slide_number, title=str(slide.get("title", "")), text=text))
    return segments


def _alignment_from_clips(segments: list[NarrationSegment], clip_paths: list[Path], renderer: FFmpegVideoRenderer) -> list[dict[str, Any]]:
    current = 0.0
    alignment: list[dict[str, Any]] = []
    for segment, clip_path in zip(segments, clip_paths, strict=True):
        duration = renderer.probe_duration(clip_path, clip_path.with_suffix(".probe.log"))
        start = current
        end = start + duration
        alignment.append(
            {
                "slide_number": segment.slide_number,
                "title": segment.title,
                "text": segment.text,
                "audio_path": str(clip_path),
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(duration, 3),
            }
        )
        current = end
    return alignment


class InitRunStage(BaseStage):
    definition = StageDefinition(
        "00_init_run",
        "Create the run folder and snapshot all run configuration.",
        (),
        "Inspect the run manifest and config snapshot before executing other stages if you want to confirm the exact run configuration.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        run_manifest = context.capture_external_input(Path(context.workspace.run_manifest_path), "run_manifest.json")  # type: ignore[attr-defined]
        config_snapshot = context.capture_external_input(Path(context.workspace.config_snapshot_path), "config.snapshot.json")  # type: ignore[attr-defined]
        return {"run_manifest": run_manifest, "config_snapshot": config_snapshot}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        outputs = {
            "run_manifest": context.output_path("run_manifest.json"),
            "config_snapshot": context.output_path("config.snapshot.json"),
        }
        copy_file(inputs["run_manifest"], outputs["run_manifest"])
        copy_file(inputs["config_snapshot"], outputs["config_snapshot"])
        return outputs

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        manifest = {
            "generated_at": now_utc(),
            "outputs": ["run_manifest.json", "config.snapshot.json"],
        }
        atomic_write_json(context.output_path("run_manifest.stage.json"), manifest)
        return StageResult(
            artifacts=[
                ArtifactRecord("run_manifest", result["run_manifest"], "Run manifest snapshot."),
                ArtifactRecord("config_snapshot", result["config_snapshot"], "Frozen config snapshot."),
                ArtifactRecord("run_stage_manifest", context.output_path("run_manifest.stage.json"), "Stage 00 output manifest."),
            ]
        )


class DownloadSourceStage(BaseStage):
    definition = StageDefinition(
        "01_download_source",
        "Download the source video with yt-dlp or reuse a local source asset.",
        ("00_init_run",),
        "If you used `--skip-download`, add the intended source video into `edits/original_video.*` before continuing.",
    )

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        adapter = _source_adapter(context.config)
        source_config = context.config["source"]
        youtube_url = str(context.run_manifest["source"].get("youtube_url") or source_config.get("youtube_url", ""))
        result = adapter.acquire(
            youtube_url=youtube_url,
            output_dir=context.paths["outputs"],
            log_dir=context.paths["logs"],
            skip_download=bool(source_config.get("skip_download", False)),
        )
        request_path = context.output_path("download_request.json")
        metadata_path = context.output_path("metadata.json")
        command_path = context.output_path("yt_dlp_command.txt")
        logs_path = context.output_path("download_logs.txt")
        atomic_write_json(request_path, result.request)
        atomic_write_json(metadata_path, result.metadata)
        atomic_write_text(command_path, result.command + "\n")
        atomic_write_text(logs_path, "\n".join(result.logs).rstrip() + "\n")
        video_path = result.video_path
        manual_recovery = None
        if video_path is None:
            manual_recovery = context.output_path("manual_recovery.md")
            atomic_write_text(
                manual_recovery,
                "# Manual recovery\n\n"
                "No source video was saved by stage 01.\n\n"
                "Options:\n"
                "- rerun without `--skip-download`\n"
                "- place a local source video into `edits/original_video.mp4` or another matching extension\n",
            )
        return {
            "request_path": request_path,
            "metadata_path": metadata_path,
            "command_path": command_path,
            "logs_path": logs_path,
            "video_path": video_path,
            "manual_recovery": manual_recovery,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        artifacts = [
            ArtifactRecord("download_request", result["request_path"], "Source acquisition request payload."),
            ArtifactRecord("source_metadata", result["metadata_path"], "yt-dlp or local source metadata."),
            ArtifactRecord("yt_dlp_command", result["command_path"], "Source acquisition command string."),
            ArtifactRecord("download_logs", result["logs_path"], "Source acquisition logs."),
        ]
        if result["video_path"] is not None:
            artifacts.append(
                ArtifactRecord(
                    "source_video",
                    result["video_path"],
                    "Source video for downstream processing.",
                    editable=True,
                )
            )
        if result["manual_recovery"] is not None:
            artifacts.append(ArtifactRecord("manual_recovery", result["manual_recovery"], "Manual recovery instructions."))
        return StageResult(artifacts=artifacts)


class ExtractMediaStage(BaseStage):
    definition = StageDefinition(
        "02_extract_media",
        "Use FFmpeg to extract audio, preview audio, media metadata, and thumbnails.",
        ("01_download_source",),
        "Inspect the extracted audio and thumbnails if the download looks unusual or later speech alignment seems off.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        source_video = context.require_input("01_download_source", "source_video")
        return {"source_video": source_video}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        renderer = _video_renderer(context.config)
        ffmpeg = context.config["ffmpeg"]
        result = renderer.extract_media_assets(
            source_video=inputs["source_video"],
            output_dir=context.paths["outputs"],
            log_dir=context.paths["logs"],
            sample_rate=int(ffmpeg.get("audio_sample_rate", 16000)),
            preview_audio_bitrate=str(ffmpeg.get("preview_audio_bitrate", "64k")),
            thumbnail_interval_seconds=int(ffmpeg.get("thumbnail_interval_seconds", 45)),
        )
        media_info_path = context.output_path("media_info.json")
        atomic_write_json(media_info_path, result.media_info)
        return {
            "source_audio_wav": result.source_audio_wav,
            "source_audio_mp3": result.source_audio_mp3,
            "preview_audio_mp3": result.preview_audio_mp3,
            "media_info": media_info_path,
            "thumbnails": result.thumbnails_dir,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("source_audio_wav", result["source_audio_wav"], "Extracted WAV audio."),
                ArtifactRecord("source_audio_mp3", result["source_audio_mp3"], "Extracted MP3 audio."),
                ArtifactRecord("preview_audio_mp3", result["preview_audio_mp3"], "Lower bitrate preview audio."),
                ArtifactRecord("media_info", result["media_info"], "FFprobe media metadata."),
                ArtifactRecord("thumbnails", result["thumbnails"], "Source thumbnails directory."),
            ]
        )


class TranscribeStage(BaseStage):
    definition = StageDefinition(
        "03_transcribe",
        "Generate or normalize a timestamped transcript from the extracted source audio.",
        ("02_extract_media",),
        "Edit `transcript_clean.md` in `edits/` if the normalized transcript needs correction before downstream stages.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        audio_path = context.require_input("02_extract_media", "source_audio_wav")
        return {"audio_path": audio_path}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        adapter = _transcription_adapter(context.config)
        transcript = adapter.transcribe(
            audio_path=inputs["audio_path"],
            output_dir=context.paths["outputs"],
            log_dir=context.paths["logs"],
        )
        raw_json_path = context.output_path("transcript_raw.json")
        clean_markdown_path = context.output_path("transcript_clean.md")
        segments_path = context.output_path("transcript_segments.json")
        subtitles_vtt_path = context.output_path("subtitles.vtt")
        subtitles_srt_path = context.output_path("subtitles.srt")
        atomic_write_json(raw_json_path, transcript.raw_json)
        atomic_write_json(segments_path, transcript.transcript_segments)
        atomic_write_text(clean_markdown_path, transcript.transcript_clean_markdown)
        copy_file(transcript.subtitles_vtt_path, subtitles_vtt_path)
        copy_file(transcript.subtitles_srt_path, subtitles_srt_path)
        return {
            "transcript_raw": raw_json_path,
            "transcript_clean": clean_markdown_path,
            "transcript_segments": segments_path,
            "subtitles_vtt": subtitles_vtt_path,
            "subtitles_srt": subtitles_srt_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("transcript_raw", result["transcript_raw"], "Raw transcription provider output."),
                ArtifactRecord("transcript_clean", result["transcript_clean"], "Normalized transcript markdown.", editable=True),
                ArtifactRecord("transcript_segments", result["transcript_segments"], "Timestamped transcript segments."),
                ArtifactRecord("subtitles_vtt", result["subtitles_vtt"], "Generated VTT subtitles."),
                ArtifactRecord("subtitles_srt", result["subtitles_srt"], "Generated SRT subtitles."),
            ]
        )


class StructureContentStage(BaseStage):
    definition = StageDefinition(
        "04_structure_content",
        "Organize the transcript into chapters, key points, candidate slide boundaries, and glossary terms.",
        ("03_transcribe",),
        "Inspect the outline artifacts here if you want to tighten slide boundaries before sending the material to NotebookLM.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        transcript_clean = context.require_input("03_transcribe", "transcript_clean")
        transcript_segments = context.require_input("03_transcribe", "transcript_segments")
        return {"transcript_clean": transcript_clean, "transcript_segments": transcript_segments}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        outline = _build_outline(
            read_text(inputs["transcript_clean"]),
            read_json(inputs["transcript_segments"], []),
            context.config,
        )
        outline_path = context.output_path("outline.json")
        outline_markdown_path = context.output_path("outline.md")
        key_points_path = context.output_path("key_points.json")
        glossary_path = context.output_path("glossary.json")
        open_questions_path = context.output_path("open_questions.md")
        atomic_write_json(outline_path, outline)
        atomic_write_json(key_points_path, outline["key_points"])
        atomic_write_json(glossary_path, outline["glossary"])
        atomic_write_text(
            outline_markdown_path,
            "# Outline\n\n"
            "## Chapters\n\n"
            + "\n".join(f"- {chapter['title']}" for chapter in outline["chapters"])
            + "\n\n## Candidate Slide Boundaries\n\n"
            + "\n".join(f"- {slide['title']}" for slide in outline["candidate_slide_boundaries"])
            + "\n",
        )
        atomic_write_text(
            open_questions_path,
            "# Open Questions\n\n"
            "- Are any slides missing visuals that need manual design?\n"
            "- Should any concepts be merged or reordered before deck generation?\n",
        )
        return {
            "outline_json": outline_path,
            "outline_md": outline_markdown_path,
            "key_points": key_points_path,
            "glossary": glossary_path,
            "open_questions": open_questions_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("outline_json", result["outline_json"], "Structured outline JSON."),
                ArtifactRecord("outline_md", result["outline_md"], "Structured outline markdown.", editable=True),
                ArtifactRecord("key_points", result["key_points"], "Key points JSON."),
                ArtifactRecord("glossary", result["glossary"], "Glossary JSON."),
                ArtifactRecord("open_questions", result["open_questions"], "Open questions markdown.", editable=True),
            ]
        )


class GenerateDeckWithNotebookLMStage(BaseStage):
    definition = StageDefinition(
        "05_generate_deck_with_notebooklm",
        "Use NotebookLM through notebooklm-mcp-cli to generate a deck spec and narration notes.",
        ("04_structure_content",),
        "Review the raw NotebookLM request, response, and generated deck artifacts carefully before approval.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        transcript_clean = context.require_input("03_transcribe", "transcript_clean")
        outline_md = context.optional_input("04_structure_content", "outline_md")
        outline_json = context.optional_input("04_structure_content", "outline_json")
        return {
            "transcript_clean": transcript_clean,
            "outline_md": outline_md,
            "outline_json": outline_json,
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        adapter = _notebooklm_adapter(context.config)
        request_path = context.output_path("notebooklm_request.json")
        response_path = context.output_path("notebooklm_response.json")
        notebook_state_path = context.output_path("notebook_state.json")
        manual_recovery_path = context.output_path("manual_recovery.md")
        notebooklm_config = context.config["notebooklm"]
        partial_error: str | None = None
        try:
            generated = adapter.generate(
                run_title=context.run_manifest["run_id"],
                transcript_path=inputs["transcript_clean"],
                outline_path=inputs["outline_md"],
                logs_dir=context.paths["logs"],
                reuse_notebook_id=notebooklm_config.get("reuse_notebook_id", ""),
            )
            deck_spec = generated.deck_spec
            raw_request = generated.request
            raw_response = generated.response
            notebook_state = {
                "notebook_id": generated.notebook_id,
                "source_ids": generated.source_ids,
                "generated_at": now_utc(),
                "partial": False,
            }
            manual_recovery = ""
        except NotebookLMAdapterError as exc:
            partial_error = str(exc)
            outline = read_json(inputs["outline_json"], {}) if inputs["outline_json"] else {"candidate_slide_boundaries": []}
            deck_spec = _fallback_deck_spec(outline)
            raw_request = exc.request or {"status": "failed_before_request"}
            raw_response = exc.response or {"error": str(exc), "partial": True}
            notebook_state = {
                "notebook_id": exc.notebook_id,
                "source_ids": exc.source_ids,
                "generated_at": now_utc(),
                "partial": True,
            }
            manual_recovery = (
                "# Manual recovery\n\n"
                f"NotebookLM generation failed: {exc}\n\n"
                "Fallback deck artifacts were written so you can edit them manually and continue with stage 06.\n"
            )
        atomic_write_json(request_path, raw_request)
        atomic_write_json(response_path, raw_response)
        atomic_write_json(notebook_state_path, notebook_state)
        if manual_recovery:
            atomic_write_text(manual_recovery_path, manual_recovery)
        deck_path = context.output_path("deck_spec.json")
        slide_titles_path = context.output_path("slide_titles.md")
        slide_content_path = context.output_path("slide_content.md")
        speaker_notes_path = context.output_path("speaker_notes.md")
        narration_script_path = context.output_path("narration_script.md")
        asset_requests_path = context.output_path("asset_requests.json")
        titles_md, content_md, notes_md, narration_md = _markdown_deck_parts(deck_spec)
        atomic_write_json(deck_path, deck_spec)
        atomic_write_text(slide_titles_path, titles_md)
        atomic_write_text(slide_content_path, content_md)
        atomic_write_text(speaker_notes_path, notes_md)
        atomic_write_text(narration_script_path, narration_md)
        atomic_write_json(asset_requests_path, deck_spec.get("asset_requests", []))
        return {
            "request_path": request_path,
            "response_path": response_path,
            "notebook_state_path": notebook_state_path,
            "deck_path": deck_path,
            "slide_titles_path": slide_titles_path,
            "slide_content_path": slide_content_path,
            "speaker_notes_path": speaker_notes_path,
            "narration_script_path": narration_script_path,
            "asset_requests_path": asset_requests_path,
            "manual_recovery_path": manual_recovery_path if manual_recovery else None,
            "partial_error": partial_error,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        notes: list[str] = []
        if result["partial_error"]:
            notes.append(f"NotebookLM fallback used because the adapter failed: {result['partial_error']}")
        artifacts = [
            ArtifactRecord("notebooklm_request", result["request_path"], "Raw NotebookLM request payload."),
            ArtifactRecord("notebooklm_response", result["response_path"], "Raw NotebookLM response payload."),
            ArtifactRecord("notebook_state", result["notebook_state_path"], "NotebookLM notebook/source state."),
            ArtifactRecord("deck_spec", result["deck_path"], "Generated deck specification.", editable=True),
            ArtifactRecord("slide_titles", result["slide_titles_path"], "Generated slide titles.", editable=True),
            ArtifactRecord("slide_content", result["slide_content_path"], "Generated slide content.", editable=True),
            ArtifactRecord("speaker_notes", result["speaker_notes_path"], "Generated speaker notes.", editable=True),
            ArtifactRecord("narration_script", result["narration_script_path"], "Generated narration script.", editable=True),
            ArtifactRecord("asset_requests", result["asset_requests_path"], "Requested visual assets."),
        ]
        if result["manual_recovery_path"] is not None:
            artifacts.append(ArtifactRecord("manual_recovery", result["manual_recovery_path"], "NotebookLM recovery guidance."))
        return StageResult(artifacts=artifacts, notes=notes)


class ReviewAndPatchDeckStage(BaseStage):
    definition = StageDefinition(
        "06_review_and_patch_deck",
        "Create a review bundle and approve or patch the deck before rendering and narration.",
        ("05_generate_deck_with_notebooklm",),
        "Edit the files under `edits/` here to change slide count, ordering, wording, visuals, and narration.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        deck_spec = context.require_input("05_generate_deck_with_notebooklm", "deck_spec")
        narration_script = context.require_input("05_generate_deck_with_notebooklm", "narration_script")
        slide_titles = context.require_input("05_generate_deck_with_notebooklm", "slide_titles")
        slide_content = context.require_input("05_generate_deck_with_notebooklm", "slide_content")
        speaker_notes = context.require_input("05_generate_deck_with_notebooklm", "speaker_notes")
        return {
            "deck_spec": deck_spec,
            "narration_script": narration_script,
            "slide_titles": slide_titles,
            "slide_content": slide_content,
            "speaker_notes": speaker_notes,
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        edits_dir = context.paths["edits"]
        output_review_dir = ensure_dir(context.output_path("review_bundle"))
        edit_seed_map = {
            "approved_deck_spec.json": inputs["deck_spec"],
            "approved_narration_script.md": inputs["narration_script"],
            "slide_titles.md": inputs["slide_titles"],
            "slide_content.md": inputs["slide_content"],
            "speaker_notes.md": inputs["speaker_notes"],
        }
        for name, source in edit_seed_map.items():
            _copy_if_missing(source, edits_dir / name)
            _copy_if_missing(source, output_review_dir / name)
        approved_deck_source = edits_dir / "approved_deck_spec.json" if (edits_dir / "approved_deck_spec.json").exists() else inputs["deck_spec"]
        approved_narration_source = edits_dir / "approved_narration_script.md" if (edits_dir / "approved_narration_script.md").exists() else inputs["narration_script"]
        approved_deck_path = context.output_path("approved_deck_spec.json")
        approved_narration_path = context.output_path("approved_narration_script.md")
        copy_file(approved_deck_source, approved_deck_path)
        copy_file(approved_narration_source, approved_narration_path)
        original_deck_text = read_text(inputs["deck_spec"])
        approved_deck_text = read_text(approved_deck_path)
        original_narration_text = read_text(inputs["narration_script"])
        approved_narration_text = read_text(approved_narration_path)
        diff_report_path = context.output_path("diff_report.md")
        atomic_write_text(
            diff_report_path,
            "# Diff Report\n\n"
            "## Deck Spec\n\n```diff\n"
            + _render_diff(original_deck_text, approved_deck_text, before_name="generated", after_name="approved")
            + "\n```\n\n## Narration Script\n\n```diff\n"
            + _render_diff(
                original_narration_text,
                approved_narration_text,
                before_name="generated",
                after_name="approved",
            )
            + "\n```\n",
        )
        return {
            "approved_deck_spec": approved_deck_path,
            "approved_narration_script": approved_narration_path,
            "diff_report": diff_report_path,
            "review_bundle": output_review_dir,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("review_bundle", result["review_bundle"], "Deck review bundle."),
                ArtifactRecord("approved_deck_spec", result["approved_deck_spec"], "Approved deck spec.", editable=True),
                ArtifactRecord("approved_narration_script", result["approved_narration_script"], "Approved narration script.", editable=True),
                ArtifactRecord("diff_report", result["diff_report"], "Deck approval diff report."),
            ]
        )


class RenderSlidesStage(BaseStage):
    definition = StageDefinition(
        "07_render_slides",
        "Render the approved deck into PPTX, slide PNGs, thumbnails, and timing hints.",
        ("06_review_and_patch_deck",),
        "Inspect the PNG slides and PPTX output before continuing if you care about layout, clipping, or pacing.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        approved_deck_spec = context.require_input("06_review_and_patch_deck", "approved_deck_spec")
        return {"approved_deck_spec": approved_deck_spec}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        deck_spec = read_json(inputs["approved_deck_spec"], {})
        renderer = _slide_renderer(context.config)
        result = renderer.render(deck_spec, context.paths["outputs"])
        timing_hints_path = context.output_path("slide_timing_hints.json")
        atomic_write_json(timing_hints_path, result.slide_timing_hints)
        return {
            "deck_pptx": result.deck_pptx_path,
            "slide_images": result.slide_images_dir,
            "thumbnails": result.thumbnails_dir,
            "slide_timing_hints": timing_hints_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("deck_pptx", result["deck_pptx"], "Rendered deck as PPTX."),
                ArtifactRecord("slide_images", result["slide_images"], "Per-slide PNG exports."),
                ArtifactRecord("thumbnails", result["thumbnails"], "Slide thumbnails."),
                ArtifactRecord("slide_timing_hints", result["slide_timing_hints"], "Timing hints derived from slide content."),
            ]
        )


class GenerateVoiceStage(BaseStage):
    definition = StageDefinition(
        "08_generate_voice",
        "Generate per-slide narration clips in the user's voice through the configured TTS adapter.",
        ("06_review_and_patch_deck",),
        "This stage requires an explicit reference voice sample. Review the text/audio map and generated clips before approval.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        approved_deck_spec = context.require_input("06_review_and_patch_deck", "approved_deck_spec")
        approved_narration_script = context.require_input("06_review_and_patch_deck", "approved_narration_script")
        return {
            "approved_deck_spec": approved_deck_spec,
            "approved_narration_script": approved_narration_script,
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        reference_audio_raw = str(context.config["tts"].get("reference_audio_path", "")).strip()
        if not reference_audio_raw:
            raise ValueError("Stage 08 requires tts.reference_audio_path or --reference-audio")
        reference_audio = Path(reference_audio_raw).expanduser()
        if not reference_audio.exists():
            raise FileNotFoundError(f"Reference audio not found: {reference_audio}")
        deck_spec = read_json(inputs["approved_deck_spec"], {})
        narration_script = read_text(inputs["approved_narration_script"])
        segments = _parse_narration_script(narration_script, deck_spec)
        text_audio_map_path = context.output_path("text_audio_map.json")
        atomic_write_json(
            text_audio_map_path,
            [
                {
                    "slide_number": segment.slide_number,
                    "title": segment.title,
                    "text": segment.text,
                }
                for segment in segments
            ],
        )
        atomic_write_text(
            context.output_path("voice_consent_warning.md"),
            "# Voice Cloning Consent Warning\n\n"
            "Use only a reference sample that you are authorized to use and that reflects explicit consent for this synthesis workflow.\n",
        )
        adapter = _tts_adapter(context.config)
        renderer = _video_renderer(context.config)
        per_slide_audio_dir = ensure_dir(context.output_path("per_slide_audio"))
        try:
            batch_result = adapter.synthesize(
                segments=segments,
                output_dir=per_slide_audio_dir,
                log_dir=context.paths["logs"],
                reference_audio_path=reference_audio,
                voice_id=str(context.config["tts"].get("voice_id", "")),
            )
        except Exception as exc:
            for segment in segments:
                renderer.generate_silence(
                    output_path=per_slide_audio_dir / f"slide-{segment.slide_number:02d}.wav",
                    duration_seconds=estimate_seconds_from_text(segment.text, int(context.config["tts"].get("words_per_minute", 145))),
                    sample_rate=int(context.config["tts"].get("sample_rate", 24000)),
                    log_path=context.paths["logs"] / f"placeholder_slide_{segment.slide_number:02d}.log",
                )
            batch_result = type("PlaceholderBatch", (), {"clips": [], "provider_payload": {"error": str(exc), "provider": "placeholder"}})()  # type: ignore[misc]
            atomic_write_text(
                context.output_path("manual_recovery.md"),
                "# Manual recovery\n\n"
                f"TTS generation failed and placeholder silence was generated instead: {exc}\n\n"
                "Replace the clips under `edits/per_slide_audio/` or rerun stage 08 with a working provider.\n",
            )
        clip_paths = sorted(per_slide_audio_dir.glob("slide-*.wav"))
        alignment = _alignment_from_clips(segments, clip_paths, renderer)
        alignment_path = context.output_path("alignment.json")
        subtitles_srt_path = context.output_path("subtitles_regenerated.srt")
        merged_wav_path = context.output_path("narration_merged.wav")
        merged_mp3_path = context.output_path("narration_merged.mp3")
        atomic_write_json(alignment_path, alignment)
        _write_srt(alignment, subtitles_srt_path)
        renderer.concat_audio(
            clip_paths,
            merged_wav_path,
            context.paths["logs"] / "merge_narration.log",
            sample_rate=int(context.config["tts"].get("sample_rate", 24000)),
        )
        renderer.transcode_mp3(merged_wav_path, merged_mp3_path, context.paths["logs"] / "merge_narration_mp3.log")
        provider_payload_path = context.output_path("tts_provider_payload.json")
        atomic_write_json(provider_payload_path, batch_result.provider_payload)
        return {
            "text_audio_map": text_audio_map_path,
            "per_slide_audio": per_slide_audio_dir,
            "alignment": alignment_path,
            "subtitles_regenerated_srt": subtitles_srt_path,
            "narration_merged_wav": merged_wav_path,
            "narration_merged_mp3": merged_mp3_path,
            "provider_payload": provider_payload_path,
            "voice_consent_warning": context.output_path("voice_consent_warning.md"),
            "manual_recovery": context.output_path("manual_recovery.md") if context.output_path("manual_recovery.md").exists() else None,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        artifacts = [
            ArtifactRecord("text_audio_map", result["text_audio_map"], "Explicit narration text to clip mapping.", editable=True),
            ArtifactRecord("per_slide_audio", result["per_slide_audio"], "Per-slide narration audio clips.", editable=True),
            ArtifactRecord("alignment", result["alignment"], "Narration alignment per slide.", editable=True),
            ArtifactRecord("subtitles_regenerated_srt", result["subtitles_regenerated_srt"], "Regenerated narration subtitles."),
            ArtifactRecord("narration_merged_wav", result["narration_merged_wav"], "Merged narration WAV.", editable=True),
            ArtifactRecord("narration_merged_mp3", result["narration_merged_mp3"], "Merged narration MP3.", editable=True),
            ArtifactRecord("tts_provider_payload", result["provider_payload"], "Raw TTS provider payload."),
            ArtifactRecord("voice_consent_warning", result["voice_consent_warning"], "Consent warning for voice cloning."),
        ]
        if result["manual_recovery"] is not None:
            artifacts.append(ArtifactRecord("manual_recovery", result["manual_recovery"], "Audio recovery instructions."))
        return StageResult(artifacts=artifacts)


class ReviewAndPatchAudioStage(BaseStage):
    definition = StageDefinition(
        "09_review_and_patch_audio",
        "Allow manual replacement or editing of per-slide audio and recompute approved timings.",
        ("08_generate_voice",),
        "Replace files under `edits/approved_per_slide_audio/` to patch narration clips, then rerun this stage to recompute timing and merged narration.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        per_slide_audio = context.require_input("08_generate_voice", "per_slide_audio")
        text_audio_map = context.require_input("08_generate_voice", "text_audio_map")
        return {"per_slide_audio": per_slide_audio, "text_audio_map": text_audio_map}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        renderer = _video_renderer(context.config)
        output_audio_dir = ensure_dir(context.output_path("approved_per_slide_audio"))
        _copy_if_missing(inputs["per_slide_audio"], output_audio_dir)
        _copy_if_missing(inputs["per_slide_audio"], context.paths["edits"] / "approved_per_slide_audio")
        _copy_if_missing(inputs["text_audio_map"], context.paths["edits"] / "approved_text_audio_map.json")
        approved_audio_source = context.paths["edits"] / "approved_per_slide_audio"
        if not approved_audio_source.exists():
            approved_audio_source = output_audio_dir
        text_audio_map_source = context.paths["edits"] / "approved_text_audio_map.json"
        mapping = read_json(text_audio_map_source, read_json(inputs["text_audio_map"], []))
        approved_text_audio_map_path = context.output_path("approved_text_audio_map.json")
        atomic_write_json(approved_text_audio_map_path, mapping)
        clip_paths = sorted(approved_audio_source.glob("slide-*.wav"))
        alignment_entries: list[dict[str, Any]] = []
        current = 0.0
        for entry, clip_path in zip(mapping, clip_paths, strict=True):
            duration = renderer.probe_duration(clip_path, context.paths["logs"] / f"{clip_path.stem}_duration.log")
            alignment_entries.append(
                {
                    "slide_number": int(entry["slide_number"]),
                    "title": entry.get("title", ""),
                    "text": entry.get("text", ""),
                    "audio_path": str(clip_path),
                    "start_sec": round(current, 3),
                    "end_sec": round(current + duration, 3),
                    "duration_sec": round(duration, 3),
                }
            )
            current += duration
        approved_alignment_path = context.output_path("approved_alignment.json")
        approved_subtitles_srt_path = context.output_path("approved_subtitles.srt")
        approved_narration_wav_path = context.output_path("approved_narration.wav")
        approved_narration_mp3_path = context.output_path("approved_narration.mp3")
        atomic_write_json(approved_alignment_path, alignment_entries)
        _write_srt(alignment_entries, approved_subtitles_srt_path)
        renderer.concat_audio(
            clip_paths,
            approved_narration_wav_path,
            context.paths["logs"] / "approved_audio_concat.log",
            sample_rate=int(context.config["tts"].get("sample_rate", 24000)),
        )
        renderer.transcode_mp3(
            approved_narration_wav_path,
            approved_narration_mp3_path,
            context.paths["logs"] / "approved_audio_mp3.log",
        )
        source_alignment = read_text(context.require_input("08_generate_voice", "alignment"))
        diff_report_path = context.output_path("audio_diff_report.md")
        atomic_write_text(
            diff_report_path,
            "# Audio Diff Report\n\n```diff\n"
            + _render_diff(source_alignment, read_text(approved_alignment_path), before_name="generated", after_name="approved")
            + "\n```\n",
        )
        return {
            "approved_per_slide_audio": output_audio_dir,
            "approved_text_audio_map": approved_text_audio_map_path,
            "approved_alignment": approved_alignment_path,
            "approved_subtitles_srt": approved_subtitles_srt_path,
            "approved_narration_wav": approved_narration_wav_path,
            "approved_narration_mp3": approved_narration_mp3_path,
            "audio_diff_report": diff_report_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("approved_per_slide_audio", result["approved_per_slide_audio"], "Approved per-slide narration clips.", editable=True),
                ArtifactRecord("approved_text_audio_map", result["approved_text_audio_map"], "Approved text-to-audio mapping.", editable=True),
                ArtifactRecord("approved_alignment", result["approved_alignment"], "Approved narration timing map.", editable=True),
                ArtifactRecord("approved_subtitles_srt", result["approved_subtitles_srt"], "Approved narration subtitles."),
                ArtifactRecord("approved_narration_wav", result["approved_narration_wav"], "Approved merged narration WAV.", editable=True),
                ArtifactRecord("approved_narration_mp3", result["approved_narration_mp3"], "Approved merged narration MP3.", editable=True),
                ArtifactRecord("audio_diff_report", result["audio_diff_report"], "Audio diff report."),
            ]
        )


class ComposeVideoStage(BaseStage):
    definition = StageDefinition(
        "10_compose_video",
        "Use FFmpeg to compose slide images and approved narration into deterministic MP4 outputs.",
        ("07_render_slides", "09_review_and_patch_audio"),
        "Review preview.mp4 and final.mp4 here if slide pacing or subtitle timing look off.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        slide_images = context.require_input("07_render_slides", "slide_images")
        approved_alignment = context.require_input("09_review_and_patch_audio", "approved_alignment")
        approved_narration_wav = context.require_input("09_review_and_patch_audio", "approved_narration_wav")
        approved_subtitles_srt = context.require_input("09_review_and_patch_audio", "approved_subtitles_srt")
        approved_deck_spec = context.require_input("06_review_and_patch_deck", "approved_deck_spec")
        return {
            "slide_images": slide_images,
            "approved_alignment": approved_alignment,
            "approved_narration_wav": approved_narration_wav,
            "approved_subtitles_srt": approved_subtitles_srt,
            "approved_deck_spec": approved_deck_spec,
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        renderer = _video_renderer(context.config)
        alignment = read_json(inputs["approved_alignment"], [])
        slide_images = sorted(inputs["slide_images"].glob("slide-*.png"))
        durations = [float(entry["duration_sec"]) for entry in alignment]
        video_renderer_config = context.config["video_renderer"]
        ffmpeg = context.config["ffmpeg"]
        composed = renderer.compose_video(
            slide_images=slide_images,
            slide_durations=durations,
            narration_audio=inputs["approved_narration_wav"],
            output_dir=context.paths["outputs"],
            log_dir=context.paths["logs"],
            video_codec=str(video_renderer_config.get("video_codec", "libx264")),
            audio_codec=str(video_renderer_config.get("audio_codec", "aac")),
            crf=int(video_renderer_config.get("crf", 20)),
            preview_crf=int(video_renderer_config.get("preview_crf", 28)),
            fps=int(ffmpeg.get("video_fps", 30)),
            preview_scale=str(ffmpeg.get("preview_scale", "1280:-2")),
        )
        chapters_path = context.output_path("chapters.json")
        deck_spec = read_json(inputs["approved_deck_spec"], {})
        chapters = []
        for slide, entry in zip(deck_spec.get("slides", []), alignment, strict=True):
            chapters.append(
                {
                    "slide_number": int(slide["slide_number"]),
                    "title": slide.get("title", ""),
                    "start_sec": entry["start_sec"],
                    "end_sec": entry["end_sec"],
                }
            )
        atomic_write_json(chapters_path, chapters)
        muxed_subtitles_vtt_path = context.output_path("muxed_subtitles.vtt")
        renderer.srt_to_vtt(inputs["approved_subtitles_srt"], muxed_subtitles_vtt_path, context.paths["logs"] / "subtitles_vtt.log")
        return {
            "preview": composed["preview"],
            "final": composed["final"],
            "chapters": chapters_path,
            "muxed_subtitles_vtt": muxed_subtitles_vtt_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("preview_mp4", result["preview"], "Preview MP4."),
                ArtifactRecord("final_mp4", result["final"], "Final MP4."),
                ArtifactRecord("chapters", result["chapters"], "Chapter timing manifest."),
                ArtifactRecord("muxed_subtitles_vtt", result["muxed_subtitles_vtt"], "Final VTT subtitles."),
            ]
        )


class QAReportStage(BaseStage):
    definition = StageDefinition(
        "11_qa_report",
        "Generate QA reports covering transcript consistency, media gaps, render failures, and timing outliers.",
        ("10_compose_video",),
        "Read the QA report before export if you want to catch missing assets or timing issues.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        transcript_clean = context.require_input("03_transcribe", "transcript_clean")
        approved_narration_script = context.require_input("06_review_and_patch_deck", "approved_narration_script")
        approved_alignment = context.require_input("09_review_and_patch_audio", "approved_alignment")
        approved_per_slide_audio = context.require_input("09_review_and_patch_audio", "approved_per_slide_audio")
        slide_images = context.require_input("07_render_slides", "slide_images")
        final_mp4 = context.require_input("10_compose_video", "final_mp4")
        return {
            "transcript_clean": transcript_clean,
            "approved_narration_script": approved_narration_script,
            "approved_alignment": approved_alignment,
            "approved_per_slide_audio": approved_per_slide_audio,
            "slide_images": slide_images,
            "final_mp4": final_mp4,
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        workspace = context.workspace
        renderer = _video_renderer(context.config)
        transcript_words = set(_plain_words(read_text(inputs["transcript_clean"])))
        narration_words = set(_plain_words(read_text(inputs["approved_narration_script"])))
        overlap = transcript_words & narration_words
        consistency_ratio = len(overlap) / max(len(narration_words), 1)
        alignment = read_json(inputs["approved_alignment"], [])
        durations = [float(entry["duration_sec"]) for entry in alignment]
        mean_duration = statistics.mean(durations) if durations else 0.0
        duration_outliers = [
            entry
            for entry in alignment
            if mean_duration > 0 and abs(float(entry["duration_sec"]) - mean_duration) > mean_duration
        ]
        missing_slides = []
        for entry in alignment:
            slide_image = inputs["slide_images"] / f"slide-{int(entry['slide_number']):02d}.png"
            if not slide_image.exists():
                missing_slides.append(int(entry["slide_number"]))
        silent_audio: list[int] = []
        clipped_audio: list[int] = []
        for clip_path in sorted(inputs["approved_per_slide_audio"].glob("slide-*.wav")):
            slide_number = int(re.search(r"(\d+)", clip_path.stem).group(1))  # type: ignore[union-attr]
            try:
                with wave.open(str(clip_path), "rb") as handle:
                    frames = handle.readframes(handle.getnframes())
                    sample_width = handle.getsampwidth()
                    if sample_width != 2 or not frames:
                        continue
                    samples = [int.from_bytes(frames[index : index + 2], byteorder="little", signed=True) for index in range(0, len(frames), 2)]
                    peak = max(abs(sample) for sample in samples) / 32768.0 if samples else 0.0
                    mean_amp = sum(abs(sample) for sample in samples) / max(len(samples), 1)
                    if mean_amp < 10:
                        silent_audio.append(slide_number)
                    if peak >= 0.99:
                        clipped_audio.append(slide_number)
            except wave.Error:
                continue
        stage_states = {
            stage_id: workspace.load_status(stage_id).get("state")  # type: ignore[attr-defined]
            for stage_id in context.run_manifest["stage_order"]
        }
        failed_renders = [stage_id for stage_id, state in stage_states.items() if state == "failed"]
        video_duration = renderer.probe_duration(inputs["final_mp4"], context.paths["logs"] / "final_duration.log")
        qa_report = {
            "transcript_vs_narration_consistency": {
                "ratio": round(consistency_ratio, 3),
                "shared_word_count": len(overlap),
            },
            "missing_slides": missing_slides,
            "duration_outliers": duration_outliers,
            "silent_audio": silent_audio,
            "clipped_audio": clipped_audio,
            "missing_media": {
                "slide_images_missing": missing_slides,
            },
            "failed_renders": failed_renders,
            "final_video_duration_sec": round(video_duration, 3),
        }
        qa_report_path = context.output_path("qa_report.json")
        qa_markdown_path = context.output_path("qa_report.md")
        atomic_write_json(qa_report_path, qa_report)
        atomic_write_text(
            qa_markdown_path,
            "# QA Report\n\n"
            f"- Transcript vs narration consistency ratio: {consistency_ratio:.3f}\n"
            f"- Missing slides: {missing_slides or 'none'}\n"
            f"- Silent audio slides: {silent_audio or 'none'}\n"
            f"- Clipped audio slides: {clipped_audio or 'none'}\n"
            f"- Failed render stages: {failed_renders or 'none'}\n"
            f"- Final video duration (sec): {video_duration:.3f}\n",
        )
        return {
            "qa_report_json": qa_report_path,
            "qa_report_md": qa_markdown_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("qa_report_json", result["qa_report_json"], "Structured QA report."),
                ArtifactRecord("qa_report_md", result["qa_report_md"], "Human-readable QA report."),
            ]
        )


class ExportStage(BaseStage):
    definition = StageDefinition(
        "12_export",
        "Export a clean deliverables folder with final outputs, transcript, subtitles, reports, and provenance.",
        ("11_qa_report",),
        "Use this stage's deliverables directory as the handoff bundle for the completed run.",
    )

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        return {
            "final_mp4": context.require_input("10_compose_video", "final_mp4"),
            "preview_mp4": context.require_input("10_compose_video", "preview_mp4"),
            "deck_pptx": context.require_input("07_render_slides", "deck_pptx"),
            "approved_deck_spec": context.require_input("06_review_and_patch_deck", "approved_deck_spec"),
            "approved_narration_script": context.require_input("06_review_and_patch_deck", "approved_narration_script"),
            "approved_narration_wav": context.require_input("09_review_and_patch_audio", "approved_narration_wav"),
            "approved_narration_mp3": context.require_input("09_review_and_patch_audio", "approved_narration_mp3"),
            "approved_subtitles_srt": context.require_input("09_review_and_patch_audio", "approved_subtitles_srt"),
            "muxed_subtitles_vtt": context.require_input("10_compose_video", "muxed_subtitles_vtt"),
            "transcript_clean": context.require_input("03_transcribe", "transcript_clean"),
            "qa_report_md": context.require_input("11_qa_report", "qa_report_md"),
            "qa_report_json": context.require_input("11_qa_report", "qa_report_json"),
        }

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        export_dir = ensure_dir(context.output_path(context.config["export"].get("folder_name", "deliverables")))
        copied: list[dict[str, Any]] = []
        for source in inputs.values():
            destination = export_dir / source.name
            copy_file(source, destination)
            copied.append(
                {
                    "file": destination.name,
                    "source_path": str(source),
                    "sha256": hash_file(destination),
                }
            )
        run_report_json_path = context.output_path("run_report.json")
        run_report_md_path = context.output_path("run_report.md")
        stage_states = {
            stage_id: context.workspace.load_status(stage_id).get("state")  # type: ignore[attr-defined]
            for stage_id in context.run_manifest["stage_order"]
        }
        atomic_write_json(run_report_json_path, {"run_id": context.run_manifest["run_id"], "stage_states": stage_states})
        atomic_write_text(
            run_report_md_path,
            "# Run Report\n\n" + "\n".join(f"- {stage_id}: {state}" for stage_id, state in stage_states.items()) + "\n",
        )
        copy_file(run_report_json_path, export_dir / run_report_json_path.name)
        copy_file(run_report_md_path, export_dir / run_report_md_path.name)
        copied.extend(
            [
                {"file": run_report_json_path.name, "source_path": str(run_report_json_path), "sha256": hash_file(run_report_json_path)},
                {"file": run_report_md_path.name, "source_path": str(run_report_md_path), "sha256": hash_file(run_report_md_path)},
            ]
        )
        provenance_manifest_path = context.output_path("provenance_manifest.json")
        atomic_write_json(
            provenance_manifest_path,
            {
                "run_id": context.run_manifest["run_id"],
                "generated_at": now_utc(),
                "deliverables": copied,
                "source": context.run_manifest["source"],
            },
        )
        copy_file(provenance_manifest_path, export_dir / provenance_manifest_path.name)
        return {
            "deliverables_dir": export_dir,
            "provenance_manifest": provenance_manifest_path,
            "run_report_json": run_report_json_path,
            "run_report_md": run_report_md_path,
        }

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord("deliverables_dir", result["deliverables_dir"], "Exported deliverables directory."),
                ArtifactRecord("provenance_manifest", result["provenance_manifest"], "Provenance manifest for exported deliverables."),
                ArtifactRecord("run_report_json", result["run_report_json"], "Structured run report."),
                ArtifactRecord("run_report_md", result["run_report_md"], "Human-readable run report."),
            ]
        )


def build_default_stages() -> list[BaseStage]:
    """Return the ordered default pipeline stage registry."""
    return [
        InitRunStage(),
        DownloadSourceStage(),
        ExtractMediaStage(),
        TranscribeStage(),
        StructureContentStage(),
        GenerateDeckWithNotebookLMStage(),
        ReviewAndPatchDeckStage(),
        RenderSlidesStage(),
        GenerateVoiceStage(),
        ReviewAndPatchAudioStage(),
        ComposeVideoStage(),
        QAReportStage(),
        ExportStage(),
    ]
