"""Shared helpers for TTS providers."""

from __future__ import annotations

import re
import wave
from pathlib import Path

from utils import run_command, slugify


def build_clone_name(*, provider: str, reference_audio_path: Path, explicit_name: str = "") -> str:
    """Create a stable default clone name when one is not configured."""
    if explicit_name.strip():
        return explicit_name.strip()
    stem = slugify(reference_audio_path.stem, fallback="voice")
    return f"lang-trade-{provider}-{stem}"[:64]


def probe_audio_duration(audio_path: Path, *, ffprobe_path: str, log_path: Path) -> float:
    """Measure an audio clip duration with ffprobe."""
    process = run_command(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        log_path=log_path,
    )
    return float(process.stdout.strip() or 0.0)


def parse_pcm_output_sample_rate(output_format: str, *, fallback: int) -> int:
    """Extract the sample rate from an ElevenLabs PCM output format."""
    match = re.fullmatch(r"pcm_(\d+)", output_format.strip())
    return int(match.group(1)) if match else fallback


def write_pcm_wav(
    output_path: Path,
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
    sample_width_bytes: int = 2,
) -> Path:
    """Wrap raw PCM bytes in a WAV container."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width_bytes)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm_bytes)
    return output_path
