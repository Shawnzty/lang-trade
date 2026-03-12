"""TTS adapters."""

from .base import NarrationSegment, TTSAdapter, TTSBatchResult, TTSClip
from .command_provider import CommandTTSAdapter
from .manual_provider import ManualTTSAdapter

__all__ = [
    "CommandTTSAdapter",
    "ManualTTSAdapter",
    "NarrationSegment",
    "TTSAdapter",
    "TTSBatchResult",
    "TTSClip",
]
