"""Transcription base types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TranscriptResult:
    """Normalized transcription outputs."""

    raw_json: dict[str, Any]
    transcript_segments: list[dict[str, Any]]
    transcript_clean_markdown: str
    subtitles_vtt_path: Path
    subtitles_srt_path: Path


class TranscriptionAdapter(ABC):
    """Abstract transcription provider."""

    @abstractmethod
    def transcribe(self, *, audio_path: Path, output_dir: Path, log_dir: Path) -> TranscriptResult:
        """Generate a timestamped transcript."""
