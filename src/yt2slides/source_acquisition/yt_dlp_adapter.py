"""yt-dlp acquisition adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..exceptions import AdapterUnavailableError
from ..utils import command_to_string
from .base import SourceAcquisitionAdapter, SourceAcquisitionResult


class YtDlpAdapter(SourceAcquisitionAdapter):
    """Acquire a source asset with yt-dlp."""

    def __init__(
        self,
        *,
        format_selector: str,
        merge_output_format: str,
        output_template: str,
        cookies_from_browser: str = "",
        write_info_json: bool = True,
    ) -> None:
        self.format_selector = format_selector
        self.merge_output_format = merge_output_format
        self.output_template = output_template
        self.cookies_from_browser = cookies_from_browser
        self.write_info_json = write_info_json

    def acquire(
        self,
        *,
        youtube_url: str,
        output_dir: Path,
        log_dir: Path,
        skip_download: bool,
    ) -> SourceAcquisitionResult:
        if not youtube_url:
            raise ValueError("youtube_url is required")
        try:
            import yt_dlp  # type: ignore
        except ImportError as exc:
            raise AdapterUnavailableError("yt-dlp is not installed") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        logs: list[str] = []

        class ListLogger:
            def debug(self, message: str) -> None:
                logs.append(message)

            def warning(self, message: str) -> None:
                logs.append(f"WARNING: {message}")

            def error(self, message: str) -> None:
                logs.append(f"ERROR: {message}")

        outtmpl = str(output_dir / self.output_template)
        options: dict[str, Any] = {
            "format": self.format_selector,
            "merge_output_format": self.merge_output_format,
            "outtmpl": outtmpl,
            "logger": ListLogger(),
            "quiet": True,
            "no_warnings": False,
            "writeinfojson": self.write_info_json,
        }
        if self.cookies_from_browser:
            options["cookiesfrombrowser"] = (self.cookies_from_browser,)
        command = [
            "yt-dlp",
            "--format",
            self.format_selector,
            "--merge-output-format",
            self.merge_output_format,
            "--output",
            outtmpl,
        ]
        if self.cookies_from_browser:
            command.extend(["--cookies-from-browser", self.cookies_from_browser])
        if skip_download:
            command.append("--skip-download")
        command.append(youtube_url)
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(youtube_url, download=not skip_download)
            sanitized = downloader.sanitize_info(info)
            video_path: Path | None = None
            if not skip_download:
                prepared = Path(downloader.prepare_filename(info))
                if prepared.exists():
                    video_path = prepared
                else:
                    matches = sorted(output_dir.glob(f"{prepared.stem}.*"))
                    if matches:
                        video_path = matches[0]
                if video_path is not None:
                    canonical = output_dir / f"original_video{video_path.suffix}"
                    if canonical.exists():
                        canonical.unlink()
                    video_path.replace(canonical)
                    video_path = canonical
        return SourceAcquisitionResult(
            video_path=video_path,
            metadata=sanitized,
            command=command_to_string(command),
            logs=logs,
            request={
                "youtube_url": youtube_url,
                "skip_download": skip_download,
                "options": {
                    "format": self.format_selector,
                    "merge_output_format": self.merge_output_format,
                    "output_template": self.output_template,
                    "cookies_from_browser": self.cookies_from_browser,
                },
            },
        )
