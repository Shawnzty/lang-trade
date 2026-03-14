"""ElevenLabs TTS adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from exceptions import AdapterError, AdapterUnavailableError
from utils import require_binary
from .base import NarrationSegment, TTSAdapter, TTSBatchResult, TTSClip
from .common import build_clone_name, parse_pcm_output_sample_rate, probe_audio_duration, write_pcm_wav
from .http_utils import build_multipart_form_data, request_bytes, request_json


class ElevenLabsTTSAdapter(TTSAdapter):
    """ElevenLabs instant voice cloning plus text-to-speech."""

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int = 24000,
        model_id: str = "eleven_multilingual_v2",
        output_format: str = "pcm_24000",
        clone_name: str = "",
        remove_background_noise: bool = False,
        description: str = "",
        labels: dict[str, str] | None = None,
        language_code: str = "",
        voice_settings: dict[str, Any] | None = None,
        enable_logging: bool = True,
        api_base_url: str = "https://api.elevenlabs.io",
        timeout_seconds: int = 120,
        ffprobe_path: str = "ffprobe",
    ) -> None:
        self.api_key = api_key
        self.sample_rate = int(sample_rate)
        self.model_id = model_id
        self.output_format = output_format
        self.clone_name = clone_name
        self.remove_background_noise = remove_background_noise
        self.description = description
        self.labels = labels or {}
        self.language_code = language_code
        self.voice_settings = voice_settings or {}
        self.enable_logging = enable_logging
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = int(timeout_seconds)
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
        if not self.api_key.strip():
            raise AdapterUnavailableError("No tts.elevenlabs.api_key is configured")
        if not reference_audio_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")
        if not self.output_format.startswith("pcm_"):
            raise AdapterUnavailableError(
                "ElevenLabs output_format must be a PCM format such as pcm_24000 so clips can be saved as WAV"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        clone_metadata: dict[str, Any]
        resolved_voice_id = voice_id.strip()
        clone_created = False
        if resolved_voice_id:
            clone_metadata = {"reused_voice_id": resolved_voice_id}
        else:
            clone_metadata = self._create_voice_clone(reference_audio_path, log_dir=log_dir)
            resolved_voice_id = str(clone_metadata.get("voice_id", "")).strip()
            if not resolved_voice_id:
                raise AdapterError("ElevenLabs clone response did not include a voice_id")
            if bool(clone_metadata.get("requires_verification", False)):
                raise AdapterError(
                    f"ElevenLabs created voice {resolved_voice_id}, but it requires verification before synthesis"
                )
            clone_created = True
        pcm_sample_rate = parse_pcm_output_sample_rate(self.output_format, fallback=self.sample_rate)
        clips: list[TTSClip] = []
        clip_payloads: list[dict[str, Any]] = []
        for segment in segments:
            audio_path = output_dir / f"slide-{segment.slide_number:02d}.wav"
            request_payload: dict[str, Any] = {
                "text": segment.text,
                "model_id": self.model_id,
            }
            if self.language_code.strip():
                request_payload["language_code"] = self.language_code.strip()
            voice_settings = _compact_mapping(self.voice_settings)
            if voice_settings:
                request_payload["voice_settings"] = voice_settings
            query = urlencode(
                {
                    "output_format": self.output_format,
                    "enable_logging": str(self.enable_logging).lower(),
                }
            )
            response = request_bytes(
                f"{self.api_base_url}/v1/text-to-speech/{resolved_voice_id}?{query}",
                method="POST",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                data=json.dumps(request_payload).encode("utf-8"),
                timeout_seconds=self.timeout_seconds,
                log_path=log_dir / f"elevenlabs_tts_slide_{segment.slide_number:02d}.log",
            )
            write_pcm_wav(audio_path, response.body, sample_rate=pcm_sample_rate)
            duration = probe_audio_duration(
                audio_path,
                ffprobe_path=self.ffprobe_path,
                log_path=log_dir / f"elevenlabs_tts_slide_{segment.slide_number:02d}_probe.log",
            )
            clips.append(
                TTSClip(
                    slide_number=segment.slide_number,
                    audio_path=audio_path,
                    duration_seconds=duration,
                    provider="elevenlabs",
                )
            )
            clip_payloads.append(
                {
                    "slide_number": segment.slide_number,
                    "duration_seconds": duration,
                    "output_format": self.output_format,
                }
            )
        return TTSBatchResult(
            clips=clips,
            provider_payload={
                "provider": "elevenlabs",
                "reference_audio_path": str(reference_audio_path),
                "voice_id": resolved_voice_id,
                "model_id": self.model_id,
                "output_format": self.output_format,
                "clone_created": clone_created,
                "clone": clone_metadata,
                "clips": clip_payloads,
            },
        )

    def _create_voice_clone(self, reference_audio_path: Path, *, log_dir: Path) -> dict[str, Any]:
        clone_name = build_clone_name(
            provider="elevenlabs",
            reference_audio_path=reference_audio_path,
            explicit_name=self.clone_name,
        )
        fields = [
            ("name", clone_name),
            ("remove_background_noise", str(self.remove_background_noise).lower()),
        ]
        if self.description.strip():
            fields.append(("description", self.description.strip()))
        if self.labels:
            fields.append(("labels", json.dumps(self.labels, sort_keys=True)))
        body, content_type, preview = build_multipart_form_data(
            fields=fields,
            files=[("files", reference_audio_path)],
        )
        return request_json(
            f"{self.api_base_url}/v1/voices/add",
            method="POST",
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": content_type,
            },
            data=body,
            timeout_seconds=self.timeout_seconds,
            log_path=log_dir / "elevenlabs_clone.log",
            request_body_preview=preview,
        )


def _compact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        compacted[key] = value
    return compacted
