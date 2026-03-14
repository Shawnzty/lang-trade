"""Local file source adapter."""

from __future__ import annotations

from pathlib import Path

from utils import copy_or_link, now_utc
from .base import SourceAcquisitionAdapter, SourceAcquisitionResult


class LocalMediaAdapter(SourceAcquisitionAdapter):
    """Treat a local video file as the acquisition result."""

    def __init__(self, local_video: str | Path) -> None:
        self.local_video = Path(local_video).expanduser().resolve()

    def acquire(
        self,
        *,
        youtube_url: str,
        output_dir: Path,
        log_dir: Path,
        skip_download: bool,
    ) -> SourceAcquisitionResult:
        if not self.local_video.exists():
            raise FileNotFoundError(f"Local video not found: {self.local_video}")
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"original_video{self.local_video.suffix}"
        copy_or_link(self.local_video, target)
        return SourceAcquisitionResult(
            video_path=target,
            metadata={
                "provider": "local_media",
                "path": str(self.local_video),
                "youtube_url": youtube_url,
                "used_at": now_utc(),
            },
            command=f"local-media {self.local_video}",
            logs=[f"Using local media: {self.local_video}"],
            request={
                "youtube_url": youtube_url,
                "local_video": str(self.local_video),
                "skip_download": skip_download,
            },
        )
