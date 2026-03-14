"""Source acquisition adapters."""

from .base import SourceAcquisitionAdapter, SourceAcquisitionResult
from .local_media_adapter import LocalMediaAdapter
from .yt_dlp_adapter import YtDlpAdapter

__all__ = [
    "LocalMediaAdapter",
    "SourceAcquisitionAdapter",
    "SourceAcquisitionResult",
    "YtDlpAdapter",
]
