"""Configuration loading and defaults."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .utils import expand_env_values, load_dotenv


DEFAULT_CONFIG: dict[str, Any] = {
    "workspace_root": "workspace",
    "runs_root": "workspace/runs",
    "source": {
        "youtube_url": "",
        "local_video": "",
        "skip_download": False,
        "title": "",
    },
    "yt_dlp": {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "output_template": "%(title).120s-%(id)s.%(ext)s",
        "cookies_from_browser": "",
        "write_info_json": True,
    },
    "ffmpeg": {
        "ffmpeg_path": "ffmpeg",
        "ffprobe_path": "ffprobe",
        "audio_sample_rate": 16000,
        "preview_audio_bitrate": "64k",
        "thumbnail_interval_seconds": 45,
        "video_fps": 30,
        "preview_scale": "1280:-2",
    },
    "transcription": {
        "provider": "whisper_cli",
        "language": "",
        "whisper_model": "turbo",
        "command_template": "",
        "extra_args": [],
    },
    "structure": {
        "target_slides_per_minute": 1.4,
        "max_slides": 16,
        "glossary_size": 12,
    },
    "notebooklm": {
        "provider": "notebooklm_mcp_cli",
        "cli_path": "nlm",
        "profile": "",
        "auth_strategy": "profile",
        "reuse_notebook_id": "",
        "retries": 3,
        "query_timeout_seconds": 180,
    },
    "tts": {
        "provider": "command",
        "command_template": "",
        "voice_id": "",
        "reference_audio_path": "",
        "sample_rate": 24000,
        "words_per_minute": 145,
    },
    "slide_renderer": {
        "provider": "python_pptx",
        "width": 1920,
        "height": 1080,
        "background_start": "#1f3b4d",
        "background_end": "#3a6b7c",
        "title_color": "#f7f4ea",
        "body_color": "#fff9ef",
        "accent_color": "#f2a65a",
        "font_name": "Aptos",
    },
    "video_renderer": {
        "provider": "ffmpeg",
        "video_codec": "libx264",
        "audio_codec": "aac",
        "crf": 20,
        "preview_crf": 28,
    },
    "export": {
        "folder_name": "deliverables",
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge dictionaries."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None, *, env_path: str | Path | None = None) -> dict[str, Any]:
    """Load YAML config and expand environment variables."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    env_values = dict(os.environ)
    env_values.update(load_dotenv(Path(env_path) if env_path else None))
    if path is None:
        return expand_env_values(config, env_values)
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    merged = deep_merge(config, payload)
    return expand_env_values(merged, env_values)


def apply_cli_overrides(
    config: dict[str, Any],
    *,
    youtube_url: str | None = None,
    local_video: str | None = None,
    skip_download: bool | None = None,
    runs_root: str | None = None,
    reference_audio_path: str | None = None,
) -> dict[str, Any]:
    """Apply CLI overrides to a loaded config."""
    updated = copy.deepcopy(config)
    if youtube_url is not None:
        updated["source"]["youtube_url"] = youtube_url
    if local_video is not None:
        updated["source"]["local_video"] = local_video
    if skip_download is not None:
        updated["source"]["skip_download"] = skip_download
    if runs_root is not None:
        updated["runs_root"] = runs_root
    if reference_audio_path is not None:
        updated["tts"]["reference_audio_path"] = reference_audio_path
    return updated
