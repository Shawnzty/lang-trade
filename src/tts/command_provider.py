"""Command-backed TTS adapter."""

from __future__ import annotations

import shlex
from pathlib import Path

from exceptions import AdapterUnavailableError
from utils import require_binary, run_command
from .base import NarrationSegment, TTSAdapter, TTSBatchResult, TTSClip
from .common import probe_audio_duration


class CommandTTSAdapter(TTSAdapter):
    """Generic command template wrapper for voice cloning providers."""

    def __init__(
        self,
        *,
        command_template: str,
        ffprobe_path: str = "ffprobe",
    ) -> None:
        self.command_template = command_template
        self.ffprobe_path = require_binary(ffprobe_path)

    def synthesize(
        self,
        *,
        segments: list[NarrationSegment],
        output_dir: Path,
        log_dir: Path,
        reference_audio_path: Path,
        voice_id: str = "",
    ) -> TTSBatchResult:
        if not self.command_template:
            raise AdapterUnavailableError("No tts.command_template is configured")
        if not reference_audio_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        clips: list[TTSClip] = []
        for segment in segments:
            audio_path = output_dir / f"slide-{segment.slide_number:02d}.wav"
            text_path = output_dir / f"slide-{segment.slide_number:02d}.txt"
            text_path.write_text(segment.text, encoding="utf-8")
            args = shlex.split(
                self.command_template.format(
                    text=segment.text,
                    input_text=str(text_path),
                    output_audio=str(audio_path),
                    reference_audio=str(reference_audio_path),
                    voice_id=voice_id,
                    slide_number=segment.slide_number,
                )
            )
            run_command(args, log_path=log_dir / f"tts_slide_{segment.slide_number:02d}.log")
            duration = self._probe_duration(audio_path, log_dir / f"tts_slide_{segment.slide_number:02d}_probe.log")
            clips.append(
                TTSClip(
                    slide_number=segment.slide_number,
                    audio_path=audio_path,
                    duration_seconds=duration,
                    provider="command",
                )
            )
        return TTSBatchResult(
            clips=clips,
            provider_payload={
                "provider": "command",
                "clip_count": len(clips),
                "reference_audio_path": str(reference_audio_path),
                "voice_id": voice_id,
            },
        )

    def _probe_duration(self, audio_path: Path, log_path: Path) -> float:
        return probe_audio_duration(audio_path, ffprobe_path=self.ffprobe_path, log_path=log_path)
