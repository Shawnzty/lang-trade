"""Structured logging helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils import append_jsonl, now_utc


def log_event(log_path: Path, event: str, **payload: Any) -> None:
    """Append a JSON log event."""
    append_jsonl(
        log_path,
        {
            "timestamp": now_utc(),
            "event": event,
            **payload,
        },
    )
