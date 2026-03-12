"""Whisper CLI transcription adapter."""

from __future__ import annotations

import json
from pathlib import Path

from ..utils import require_binary, run_command
from .base import TranscriptResult, TranscriptionAdapter


class WhisperCliTranscriptionAdapter(TranscriptionAdapter):
    """Use the `whisper` CLI."""

    def __init__(
        self,
        *,
        binary: str = "whisper",
        model: str = "turbo",
        language: str = "",
        extra_args: list[str] | None = None,
    ) -> None:
        self.binary = require_binary(binary)
        self.model = model
        self.language = language
        self.extra_args = extra_args or []

    def transcribe(self, *, audio_path: Path, output_dir: Path, log_dir: Path) -> TranscriptResult:
        raw_dir = output_dir / "whisper_raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        args = [
            self.binary,
            str(audio_path),
            "--output_dir",
            str(raw_dir),
            "--output_format",
            "all",
            "--model",
            self.model,
            "--word_timestamps",
            "True",
        ]
        if self.language:
            args.extend(["--language", self.language])
        args.extend(self.extra_args)
        run_command(args, log_path=log_dir / "whisper.log")
        raw_json_path = self._resolve(raw_dir, audio_path, "json")
        vtt_path = self._resolve(raw_dir, audio_path, "vtt")
        srt_path = self._resolve(raw_dir, audio_path, "srt")
        txt_path = self._resolve(raw_dir, audio_path, "txt", required=False)
        raw_json = json.loads(raw_json_path.read_text(encoding="utf-8"))
        segments = list(raw_json.get("segments", []))
        transcript_text = txt_path.read_text(encoding="utf-8").strip() if txt_path else raw_json.get("text", "")
        transcript_clean = "# Transcript\n\n" + transcript_text.strip() + "\n"
        return TranscriptResult(
            raw_json=raw_json,
            transcript_segments=segments,
            transcript_clean_markdown=transcript_clean,
            subtitles_vtt_path=vtt_path,
            subtitles_srt_path=srt_path,
        )

    def _resolve(self, output_dir: Path, audio_path: Path, suffix: str, *, required: bool = True) -> Path | None:
        candidates = [
            output_dir / f"{audio_path.name}.{suffix}",
            output_dir / f"{audio_path.stem}.{suffix}",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        if required:
            raise RuntimeError(f"Whisper did not produce a .{suffix} output")
        return None
