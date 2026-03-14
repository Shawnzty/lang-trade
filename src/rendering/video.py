"""FFmpeg-based media processing and video composition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils import atomic_write_text, ensure_dir, read_text, require_binary, run_command


@dataclass
class MediaExtractionResult:
    """Stage 02 outputs."""

    source_audio_wav: Path
    source_audio_mp3: Path
    preview_audio_mp3: Path
    media_info: dict[str, Any]
    thumbnails_dir: Path


class FFmpegVideoRenderer:
    """Use FFmpeg for extraction, audio merging, and slideshow composition."""

    def __init__(self, *, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> None:
        self.ffmpeg_path = require_binary(ffmpeg_path)
        self.ffprobe_path = require_binary(ffprobe_path)

    def extract_media_assets(
        self,
        *,
        source_video: Path,
        output_dir: Path,
        log_dir: Path,
        sample_rate: int,
        preview_audio_bitrate: str,
        thumbnail_interval_seconds: int,
    ) -> MediaExtractionResult:
        source_audio_wav = output_dir / "source_audio.wav"
        source_audio_mp3 = output_dir / "source_audio.mp3"
        preview_audio_mp3 = output_dir / "source_audio_preview.mp3"
        thumbnails_dir = ensure_dir(output_dir / "thumbnails")
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-c:a",
                "pcm_s16le",
                str(source_audio_wav),
            ],
            log_path=log_dir / "extract_audio_wav.log",
        )
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_video),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(source_audio_mp3),
            ],
            log_path=log_dir / "extract_audio_mp3.log",
        )
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_video),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                preview_audio_bitrate,
                str(preview_audio_mp3),
            ],
            log_path=log_dir / "extract_audio_preview.log",
        )
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_video),
                "-vf",
                f"fps=1/{max(thumbnail_interval_seconds, 1)}",
                str(thumbnails_dir / "thumb-%04d.jpg"),
            ],
            log_path=log_dir / "extract_thumbnails.log",
            check=False,
        )
        media_info = self.probe_json(source_video, log_dir / "ffprobe_source.log")
        return MediaExtractionResult(
            source_audio_wav=source_audio_wav,
            source_audio_mp3=source_audio_mp3,
            preview_audio_mp3=preview_audio_mp3,
            media_info=media_info,
            thumbnails_dir=thumbnails_dir,
        )

    def probe_json(self, media_path: Path, log_path: Path) -> dict[str, Any]:
        """Run ffprobe and parse JSON output."""
        process = run_command(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format:stream",
                "-print_format",
                "json",
                str(media_path),
            ],
            log_path=log_path,
        )
        return json.loads(process.stdout)

    def probe_duration(self, media_path: Path, log_path: Path) -> float:
        """Probe one media duration in seconds."""
        process = run_command(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            log_path=log_path,
        )
        return float(process.stdout.strip() or 0.0)

    def concat_audio(self, clip_paths: list[Path], output_path: Path, log_path: Path, *, sample_rate: int) -> Path:
        """Concatenate audio clips deterministically."""
        concat_manifest = output_path.parent / "audio_concat.txt"
        atomic_write_text(
            concat_manifest,
            "".join(f"file '{clip.resolve().as_posix()}'\n" for clip in clip_paths),
        )
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_manifest),
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            log_path=log_path,
        )
        return output_path

    def transcode_mp3(self, source_audio: Path, output_path: Path, log_path: Path) -> Path:
        """Transcode audio to MP3."""
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_audio),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(output_path),
            ],
            log_path=log_path,
        )
        return output_path

    def srt_to_vtt(self, source_srt: Path, output_vtt: Path, log_path: Path) -> Path:
        """Convert SRT to VTT using ffmpeg."""
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(source_srt),
                str(output_vtt),
            ],
            log_path=log_path,
        )
        if not output_vtt.exists():
            text = read_text(source_srt)
            atomic_write_text(output_vtt, "WEBVTT\n\n" + text)
        return output_vtt

    def generate_silence(self, *, output_path: Path, duration_seconds: float, sample_rate: int, log_path: Path) -> Path:
        """Generate silent mono wav audio."""
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={sample_rate}:cl=mono",
                "-t",
                f"{duration_seconds:.3f}",
                str(output_path),
            ],
            log_path=log_path,
        )
        return output_path

    def compose_video(
        self,
        *,
        slide_images: list[Path],
        slide_durations: list[float],
        narration_audio: Path,
        output_dir: Path,
        log_dir: Path,
        video_codec: str,
        audio_codec: str,
        crf: int,
        preview_crf: int,
        fps: int,
        preview_scale: str,
    ) -> dict[str, Path]:
        """Create preview and final MP4 outputs."""
        segments_dir = ensure_dir(output_dir / "segments")
        segment_paths: list[Path] = []
        for slide_number, (image_path, duration) in enumerate(zip(slide_images, slide_durations, strict=True), start=1):
            segment_path = segments_dir / f"slide-{slide_number:02d}.mp4"
            run_command(
                [
                    self.ffmpeg_path,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(image_path),
                    "-t",
                    f"{duration:.3f}",
                    "-vf",
                    f"fps={fps},format=yuv420p",
                    "-c:v",
                    video_codec,
                    "-crf",
                    str(crf),
                    "-pix_fmt",
                    "yuv420p",
                    str(segment_path),
                ],
                log_path=log_dir / f"segment_{slide_number:02d}.log",
            )
            segment_paths.append(segment_path)
        concat_manifest = segments_dir / "video_concat.txt"
        atomic_write_text(
            concat_manifest,
            "".join(f"file '{segment.resolve().as_posix()}'\n" for segment in segment_paths),
        )
        video_only = output_dir / "video_only.mp4"
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_manifest),
                "-c",
                "copy",
                str(video_only),
            ],
            log_path=log_dir / "concat_video.log",
        )
        final_path = output_dir / "final.mp4"
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(video_only),
                "-i",
                str(narration_audio),
                "-c:v",
                "copy",
                "-c:a",
                audio_codec,
                "-shortest",
                str(final_path),
            ],
            log_path=log_dir / "final_mux.log",
        )
        preview_path = output_dir / "preview.mp4"
        run_command(
            [
                self.ffmpeg_path,
                "-y",
                "-i",
                str(final_path),
                "-vf",
                f"scale={preview_scale}",
                "-c:v",
                video_codec,
                "-crf",
                str(preview_crf),
                "-c:a",
                audio_codec,
                str(preview_path),
            ],
            log_path=log_dir / "preview_render.log",
        )
        return {
            "video_only": video_only,
            "final": final_path,
            "preview": preview_path,
        }
