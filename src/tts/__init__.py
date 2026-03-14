"""TTS adapters."""

from .base import NarrationSegment, TTSAdapter, TTSBatchResult, TTSClip
from .command_provider import CommandTTSAdapter
from .elevenlabs_provider import ElevenLabsTTSAdapter
from .fish_audio_provider import FishAudioTTSAdapter
from .manual_provider import ManualTTSAdapter

__all__ = [
    "CommandTTSAdapter",
    "ElevenLabsTTSAdapter",
    "FishAudioTTSAdapter",
    "ManualTTSAdapter",
    "NarrationSegment",
    "TTSAdapter",
    "TTSBatchResult",
    "TTSClip",
]
