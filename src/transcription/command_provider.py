"""Generic command-backed transcription adapter."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from exceptions import AdapterUnavailableError
from utils import run_command
from .base import TranscriptResult, TranscriptionAdapter


class CommandTranscriptionAdapter(TranscriptionAdapter):
    """Execute a configured wrapper command for transcription."""

    def __init__(self, command_template: str) -> None:
        self.command_template = command_template

    def transcribe(self, *, audio_path: Path, output_dir: Path, log_dir: Path) -> TranscriptResult:
        if not self.command_template:
            raise AdapterUnavailableError("No transcription command_template is configured")
        args = shlex.split(
            self.command_template.format(
                input_audio=str(audio_path),
                output_dir=str(output_dir),
            )
        )
        run_command(args, log_path=log_dir / "transcription_command.log")
        raw_json_path = output_dir / "transcript_raw.json"
        segments_path = output_dir / "transcript_segments.json"
        clean_markdown_path = output_dir / "transcript_clean.md"
        subtitles_vtt_path = output_dir / "subtitles.vtt"
        subtitles_srt_path = output_dir / "subtitles.srt"
        if not raw_json_path.exists() or not segments_path.exists():
            raise RuntimeError("Command adapter did not write transcript_raw.json and transcript_segments.json")
        return TranscriptResult(
            raw_json=json.loads(raw_json_path.read_text(encoding="utf-8")),
            transcript_segments=json.loads(segments_path.read_text(encoding="utf-8")),
            transcript_clean_markdown=clean_markdown_path.read_text(encoding="utf-8"),
            subtitles_vtt_path=subtitles_vtt_path,
            subtitles_srt_path=subtitles_srt_path,
        )
