"""Transcription adapters."""

from .base import TranscriptResult, TranscriptionAdapter
from .command_provider import CommandTranscriptionAdapter
from .whisper_cli import WhisperCliTranscriptionAdapter

__all__ = [
    "CommandTranscriptionAdapter",
    "TranscriptResult",
    "TranscriptionAdapter",
    "WhisperCliTranscriptionAdapter",
]
