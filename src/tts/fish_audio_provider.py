"""Fish Audio TTS adapter."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from exceptions import AdapterError, AdapterUnavailableError
from utils import require_binary
from .base import NarrationSegment, TTSAdapter, TTSBatchResult, TTSClip
from .common import build_clone_name, probe_audio_duration
from .http_utils import build_multipart_form_data, request_bytes, request_json


class FishAudioTTSAdapter(TTSAdapter):
    """Fish Audio voice cloning and TTS integration."""

    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int = 24000,
        model: str = "s1",
        clone_name: str = "",
        visibility: str = "private",
        train_mode: str = "fast",
        enhance_audio_quality: bool = True,
        latency: str = "normal",
        normalize: bool = True,
        reference_text: str = "",
        api_base_url: str = "https://api.fish.audio",
        timeout_seconds: int = 120,
        clone_poll_attempts: int = 1,
        clone_poll_interval_seconds: float = 2.0,
        ffprobe_path: str = "ffprobe",
    ) -> None:
        self.api_key = api_key
        self.sample_rate = int(sample_rate)
        self.model = model
        self.clone_name = clone_name
        self.visibility = visibility
        self.train_mode = train_mode
        self.enhance_audio_quality = enhance_audio_quality
        self.latency = latency
        self.normalize = normalize
        self.reference_text = reference_text
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = int(timeout_seconds)
        self.clone_poll_attempts = max(int(clone_poll_attempts), 1)
        self.clone_poll_interval_seconds = float(clone_poll_interval_seconds)
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
            raise AdapterUnavailableError("No tts.fish_audio.api_key is configured")
        if not reference_audio_path.exists():
            raise FileNotFoundError(f"Reference audio not found: {reference_audio_path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        clone_metadata: dict[str, Any]
        reference_id = voice_id.strip()
        clone_created = False
        if reference_id:
            clone_metadata = {"reused_reference_id": reference_id}
        else:
            clone_metadata = self._create_voice_clone(reference_audio_path, log_dir=log_dir)
            reference_id = str(clone_metadata.get("_id") or clone_metadata.get("id") or "").strip()
            if not reference_id:
                raise AdapterError("Fish Audio clone response did not include a reference id")
            clone_created = True
        clips: list[TTSClip] = []
        clip_payloads: list[dict[str, Any]] = []
        for segment in segments:
            audio_path = output_dir / f"slide-{segment.slide_number:02d}.wav"
            request_payload = {
                "text": segment.text,
                "reference_id": reference_id,
                "format": "wav",
                "sample_rate": self.sample_rate,
                "latency": self.latency,
                "normalize": self.normalize,
            }
            response = request_bytes(
                f"{self.api_base_url}/v1/tts",
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "model": self.model,
                },
                data=json.dumps(request_payload).encode("utf-8"),
                timeout_seconds=self.timeout_seconds,
                log_path=log_dir / f"fish_audio_tts_slide_{segment.slide_number:02d}.log",
            )
            audio_path.write_bytes(response.body)
            duration = probe_audio_duration(
                audio_path,
                ffprobe_path=self.ffprobe_path,
                log_path=log_dir / f"fish_audio_tts_slide_{segment.slide_number:02d}_probe.log",
            )
            clips.append(
                TTSClip(
                    slide_number=segment.slide_number,
                    audio_path=audio_path,
                    duration_seconds=duration,
                    provider="fish_audio",
                )
            )
            clip_payloads.append(
                {
                    "slide_number": segment.slide_number,
                    "duration_seconds": duration,
                    "content_type": response.headers.get("Content-Type", ""),
                }
            )
        return TTSBatchResult(
            clips=clips,
            provider_payload={
                "provider": "fish_audio",
                "reference_audio_path": str(reference_audio_path),
                "reference_id": reference_id,
                "model": self.model,
                "clone_created": clone_created,
                "clone": clone_metadata,
                "clips": clip_payloads,
            },
        )

    def _create_voice_clone(self, reference_audio_path: Path, *, log_dir: Path) -> dict[str, Any]:
        clone_name = build_clone_name(
            provider="fish-audio",
            reference_audio_path=reference_audio_path,
            explicit_name=self.clone_name,
        )
        fields = [
            ("visibility", self.visibility),
            ("type", "tts"),
            ("title", clone_name),
            ("train_mode", self.train_mode),
            ("enhance_audio_quality", str(self.enhance_audio_quality).lower()),
        ]
        if self.reference_text.strip():
            fields.append(("texts", self.reference_text.strip()))
        body, content_type, preview = build_multipart_form_data(
            fields=fields,
            files=[("voices", reference_audio_path)],
        )
        payload = request_json(
            f"{self.api_base_url}/model",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": content_type,
            },
            data=body,
            timeout_seconds=self.timeout_seconds,
            log_path=log_dir / "fish_audio_clone.log",
            request_body_preview=preview,
        )
        model_id = str(payload.get("_id") or payload.get("id") or "").strip()
        if not model_id:
            raise AdapterError("Fish Audio clone response did not include a model id")
        return self._wait_for_clone_ready(model_id, initial_payload=payload, log_dir=log_dir)

    def _wait_for_clone_ready(self, model_id: str, *, initial_payload: dict[str, Any], log_dir: Path) -> dict[str, Any]:
        payload = initial_payload
        for attempt in range(self.clone_poll_attempts):
            state = str(payload.get("state", "")).strip().lower()
            if state in {"", "created", "trained", "ready", "available", "completed"}:
                return payload
            if state in {"error", "failed", "not_exist"}:
                raise AdapterError(f"Fish Audio clone {model_id} entered an unusable state: {state}")
            if attempt == self.clone_poll_attempts - 1:
                break
            time.sleep(self.clone_poll_interval_seconds)
            payload = request_json(
                f"{self.api_base_url}/model/{model_id}",
                method="GET",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout_seconds=self.timeout_seconds,
                log_path=log_dir / f"fish_audio_clone_status_{attempt + 1:02d}.log",
            )
        final_state = str(payload.get("state", "")).strip().lower()
        if final_state in {"created", "trained", "ready", "available", "completed"}:
            return payload
        raise AdapterError(f"Fish Audio clone {model_id} was not ready in time (state={final_state or 'unknown'})")
