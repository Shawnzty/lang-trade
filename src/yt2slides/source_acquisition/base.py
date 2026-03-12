"""Source acquisition base types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SourceAcquisitionResult:
    """Outputs from source acquisition."""

    video_path: Path | None
    metadata: dict[str, Any]
    command: str
    logs: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)


class SourceAcquisitionAdapter(ABC):
    """Abstract source acquisition adapter."""

    @abstractmethod
    def acquire(
        self,
        *,
        youtube_url: str,
        output_dir: Path,
        log_dir: Path,
        skip_download: bool,
    ) -> SourceAcquisitionResult:
        """Acquire a source video."""
