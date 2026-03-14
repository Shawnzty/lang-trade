from __future__ import annotations

import sys
import wave
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import DEFAULT_CONFIG, deep_merge
from stages import pipeline_stages as stage_module
from tts.base import NarrationSegment
from tts.elevenlabs_provider import ElevenLabsTTSAdapter
from tts.fish_audio_provider import FishAudioTTSAdapter
from tts.http_utils import HttpResponse
import tts.elevenlabs_provider as elevenlabs_module
import tts.fish_audio_provider as fish_audio_module


def _wav_bytes(*, sample_rate: int = 24000, frames: int = 2400) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def test_fish_audio_adapter_clones_voice_and_generates_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(_wav_bytes())
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(fish_audio_module, "require_binary", lambda binary: binary)
    monkeypatch.setattr(
        fish_audio_module,
        "request_json",
        lambda url, **kwargs: (
            calls.append(("json", url)) or {"_id": "fish-model-123", "state": "created", "title": "voice"}
        ),
    )
    monkeypatch.setattr(
        fish_audio_module,
        "request_bytes",
        lambda url, **kwargs: (
            calls.append(("bytes", url))
            or HttpResponse(status_code=200, headers={"Content-Type": "audio/wav"}, body=_wav_bytes(frames=800))
        ),
    )
    monkeypatch.setattr(fish_audio_module, "probe_audio_duration", lambda *args, **kwargs: 1.25)

    adapter = FishAudioTTSAdapter(api_key="fish-key", ffprobe_path="ffprobe")
    result = adapter.synthesize(
        segments=[NarrationSegment(slide_number=1, title="Intro", text="Hello from Fish Audio.")],
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
        reference_audio_path=reference_audio,
    )

    assert calls[0] == ("json", "https://api.fish.audio/model")
    assert calls[1] == ("bytes", "https://api.fish.audio/v1/tts")
    assert result.provider_payload["provider"] == "fish_audio"
    assert result.provider_payload["reference_id"] == "fish-model-123"
    assert result.provider_payload["clone_created"] is True
    assert result.clips[0].provider == "fish_audio"
    assert result.clips[0].duration_seconds == 1.25
    assert (tmp_path / "output" / "slide-01.wav").exists()


def test_elevenlabs_adapter_clones_voice_and_generates_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_audio = tmp_path / "reference.wav"
    reference_audio.write_bytes(_wav_bytes())
    pcm_bytes = b"\x01\x00" * 320
    requested_urls: list[str] = []

    monkeypatch.setattr(elevenlabs_module, "require_binary", lambda binary: binary)
    monkeypatch.setattr(
        elevenlabs_module,
        "request_json",
        lambda url, **kwargs: {"voice_id": "voice-123", "requires_verification": False},
    )

    def fake_request_bytes(url: str, **kwargs: object) -> HttpResponse:
        requested_urls.append(url)
        return HttpResponse(status_code=200, headers={"Content-Type": "audio/pcm"}, body=pcm_bytes)

    monkeypatch.setattr(elevenlabs_module, "request_bytes", fake_request_bytes)
    monkeypatch.setattr(elevenlabs_module, "probe_audio_duration", lambda *args, **kwargs: 0.5)

    adapter = ElevenLabsTTSAdapter(api_key="eleven-key", ffprobe_path="ffprobe")
    result = adapter.synthesize(
        segments=[NarrationSegment(slide_number=1, title="Intro", text="Hello from ElevenLabs.")],
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
        reference_audio_path=reference_audio,
    )

    assert requested_urls == [
        "https://api.elevenlabs.io/v1/text-to-speech/voice-123?output_format=pcm_24000&enable_logging=true"
    ]
    assert result.provider_payload["provider"] == "elevenlabs"
    assert result.provider_payload["voice_id"] == "voice-123"
    assert result.provider_payload["clone_created"] is True
    assert result.clips[0].provider == "elevenlabs"
    wav_path = tmp_path / "output" / "slide-01.wav"
    assert wav_path.exists()
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getnframes() == len(pcm_bytes) // 2


def test_tts_adapter_factory_supports_fish_audio_and_elevenlabs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fish_kwargs: dict[str, object] = {}
    eleven_kwargs: dict[str, object] = {}

    class FakeFishAudioAdapter:
        def __init__(self, **kwargs: object) -> None:
            fish_kwargs.update(kwargs)

    class FakeElevenLabsAdapter:
        def __init__(self, **kwargs: object) -> None:
            eleven_kwargs.update(kwargs)

    monkeypatch.setattr(stage_module, "FishAudioTTSAdapter", FakeFishAudioAdapter)
    monkeypatch.setattr(stage_module, "ElevenLabsTTSAdapter", FakeElevenLabsAdapter)

    fish_config = deep_merge(
        DEFAULT_CONFIG,
        {
            "ffmpeg": {"ffprobe_path": "custom-ffprobe"},
            "tts": {
                "provider": "fish_audio",
                "sample_rate": 22050,
                "clone_voice_name": "Fish Voice",
                "fish_audio": {
                    "api_key": "fish-key",
                    "normalize": False,
                },
            },
        },
    )
    eleven_config = deep_merge(
        DEFAULT_CONFIG,
        {
            "ffmpeg": {"ffprobe_path": "custom-ffprobe"},
            "tts": {
                "provider": "elevenlabs",
                "clone_voice_name": "Eleven Voice",
                "elevenlabs": {
                    "api_key": "eleven-key",
                    "output_format": "pcm_44100",
                    "enable_logging": False,
                },
            },
        },
    )

    stage_module._tts_adapter(fish_config)
    stage_module._tts_adapter(eleven_config)

    assert fish_kwargs["api_key"] == "fish-key"
    assert fish_kwargs["sample_rate"] == 22050
    assert fish_kwargs["clone_name"] == "Fish Voice"
    assert fish_kwargs["normalize"] is False
    assert fish_kwargs["ffprobe_path"] == "custom-ffprobe"
    assert eleven_kwargs["api_key"] == "eleven-key"
    assert eleven_kwargs["clone_name"] == "Eleven Voice"
    assert eleven_kwargs["output_format"] == "pcm_44100"
    assert eleven_kwargs["enable_logging"] is False
    assert eleven_kwargs["ffprobe_path"] == "custom-ffprobe"
