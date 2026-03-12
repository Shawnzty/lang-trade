"""TTS base types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NarrationSegment:
    """Narration text for one slide."""

    slide_number: int
    title: str
    text: str


@dataclass
class TTSClip:
    """One generated clip."""

    slide_number: int
    audio_path: Path
    duration_seconds: float
    provider: str


@dataclass
class TTSBatchResult:
    """Outputs from a TTS batch."""

    clips: list[TTSClip]
    provider_payload: dict[str, Any] = field(default_factory=dict)


class TTSAdapter(ABC):
    """Abstract TTS/voice cloning provider."""

    @abstractmethod
    def synthesize(
        self,
        *,
        segments: list[NarrationSegment],
        output_dir: Path,
        log_dir: Path,
        reference_audio_path: Path,
        voice_id: str = "",
    ) -> TTSBatchResult:
        """Generate narration audio clips."""
