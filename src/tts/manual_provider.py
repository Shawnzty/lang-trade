"""Manual TTS placeholder."""

from __future__ import annotations

from pathlib import Path

from exceptions import AdapterUnavailableError
from .base import NarrationSegment, TTSAdapter, TTSBatchResult


class ManualTTSAdapter(TTSAdapter):
    """Adapter used when the user wants to provide audio manually."""

    def synthesize(
        self,
        *,
        segments: list[NarrationSegment],
        output_dir: Path,
        log_dir: Path,
        reference_audio_path: Path,
        voice_id: str = "",
    ) -> TTSBatchResult:
        raise AdapterUnavailableError(
            "TTS provider is set to manual. Generate per-slide clips externally and place them in edits/."
        )
