"""Rendering adapters."""

from .slides import SlideRenderingResult, SlideRenderer
from .video import FFmpegVideoRenderer, MediaExtractionResult

__all__ = [
    "FFmpegVideoRenderer",
    "MediaExtractionResult",
    "SlideRenderer",
    "SlideRenderingResult",
]
