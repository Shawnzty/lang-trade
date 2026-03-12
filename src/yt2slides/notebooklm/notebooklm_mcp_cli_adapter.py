"""Adapter around notebooklm-mcp-cli."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..utils import extract_json_object, require_binary, run_command
from .base import NotebookLMAdapter, NotebookLMAdapterError, NotebookLMGenerationResult

T = TypeVar("T")


class NotebookLMMcpCliAdapter(NotebookLMAdapter):
    """Isolate all `notebooklm-mcp-cli` calls inside one adapter."""

    def __init__(
        self,
        *,
        cli_path: str = "nlm",
        profile: str = "",
        retries: int = 3,
        query_timeout_seconds: int = 180,
    ) -> None:
        self.cli_path = require_binary(cli_path)
        self.profile = profile
        self.retries = max(retries, 1)
        self.query_timeout_seconds = query_timeout_seconds

    def generate(
        self,
        *,
        run_title: str,
        transcript_path: Path,
        outline_path: Path | None,
        logs_dir: Path,
        reuse_notebook_id: str = "",
    ) -> NotebookLMGenerationResult:
        notebook_id = reuse_notebook_id
        source_ids: list[str] = []
        request = self._build_request(transcript_path, outline_path)
        response: dict[str, Any] = {}
        try:
            if not notebook_id:
                notebook_id = self._with_retries(
                    "create_notebook",
                    lambda attempt: self._create_notebook(run_title, logs_dir, attempt),
                )
            source_ids.append(
                self._with_retries(
                    "add_transcript",
                    lambda attempt: self._add_source(notebook_id, transcript_path, "transcript", logs_dir, attempt),
                )
            )
            if outline_path and outline_path.exists():
                source_ids.append(
                    self._with_retries(
                        "add_outline",
                        lambda attempt: self._add_source(notebook_id, outline_path, "outline", logs_dir, attempt),
                    )
                )
            response = self._with_retries(
                "query_notebook",
                lambda attempt: self._query_notebook(notebook_id, request["prompt"], logs_dir, attempt),
            )
            raw_answer = str(response.get("answer", ""))
            deck_spec = self._normalize_deck_spec(extract_json_object(raw_answer))
            return NotebookLMGenerationResult(
                notebook_id=notebook_id,
                source_ids=source_ids,
                request=request,
                response=response,
                deck_spec=deck_spec,
                raw_answer=raw_answer,
            )
        except Exception as exc:
            raise NotebookLMAdapterError(
                str(exc),
                notebook_id=notebook_id,
                source_ids=source_ids,
                request=request,
                response=response,
            ) from exc

    def _base_args(self) -> list[str]:
        args = [self.cli_path]
        if self.profile:
            args.extend(["--profile", self.profile])
        return args

    def _with_retries(self, label: str, func: Callable[[int], T]) -> T:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return func(attempt)
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"{label} failed after {self.retries} attempts: {last_error}")

    def _create_notebook(self, title: str, logs_dir: Path, attempt: int) -> str:
        process = run_command(
            self._base_args() + ["notebook", "create", title],
            log_path=logs_dir / f"create_notebook_attempt_{attempt}.log",
        )
        match = re.search(r"ID:\s*([A-Za-z0-9\-]+)", process.stdout)
        if not match:
            raise RuntimeError("Unable to parse NotebookLM notebook id")
        return match.group(1)

    def _add_source(self, notebook_id: str, file_path: Path, stem: str, logs_dir: Path, attempt: int) -> str:
        process = run_command(
            self._base_args() + ["source", "add", notebook_id, "--file", str(file_path), "--wait"],
            log_path=logs_dir / f"add_{stem}_attempt_{attempt}.log",
        )
        match = re.search(r"Source ID:\s*([A-Za-z0-9\-]+)", process.stdout)
        if not match:
            raise RuntimeError(f"Unable to parse source id for {stem}")
        return match.group(1)

    def _query_notebook(self, notebook_id: str, prompt: str, logs_dir: Path, attempt: int) -> dict[str, Any]:
        process = run_command(
            self._base_args()
            + [
                "notebook",
                "query",
                notebook_id,
                prompt,
                "--json",
                "--timeout",
                str(self.query_timeout_seconds),
            ],
            log_path=logs_dir / f"query_notebook_attempt_{attempt}.log",
        )
        return json.loads(process.stdout)

    def _build_request(self, transcript_path: Path, outline_path: Path | None) -> dict[str, Any]:
        outline_hint = (
            f"Use {outline_path.name} to refine slide boundaries and sequencing. "
            if outline_path and outline_path.exists()
            else ""
        )
        return {
            "prompt": (
                "Create a narrated slide deck specification from the uploaded source material. "
                f"{outline_hint}"
                "Return strict JSON only. Schema: "
                "{"
                '"deck_title": string, '
                '"deck_summary": string, '
                '"slides": ['
                "{"
                '"slide_number": integer, '
                '"title": string, '
                '"objective": string, '
                '"on_slide_text": string, '
                '"bullets": [string], '
                '"suggested_visual": string, '
                '"speaker_notes": string, '
                '"narration_text": string, '
                '"estimated_duration_sec": number'
                "}"
                "], "
                '"asset_requests": [{"slide_number": integer, "request": string, "priority": string}]'
                "}. "
                f"Source transcript file: {transcript_path.name}."
            )
        }

    def _normalize_deck_spec(self, payload: dict[str, Any]) -> dict[str, Any]:
        slides: list[dict[str, Any]] = []
        for index, slide in enumerate(payload.get("slides", []), start=1):
            bullets = list(slide.get("bullets") or slide.get("content") or [])
            narration_text = str(slide.get("narration_text") or slide.get("narration_script") or "")
            slides.append(
                {
                    "slide_number": int(slide.get("slide_number", index)),
                    "title": str(slide.get("title", f"Slide {index}")),
                    "objective": str(slide.get("objective", "")),
                    "on_slide_text": str(slide.get("on_slide_text", "")),
                    "bullets": bullets,
                    "suggested_visual": str(slide.get("suggested_visual") or ", ".join(slide.get("visual_requests", []))),
                    "speaker_notes": str(slide.get("speaker_notes", "")),
                    "narration_text": narration_text,
                    "estimated_duration_sec": float(slide.get("estimated_duration_sec", 0) or 0),
                }
            )
        return {
            "deck_title": str(payload.get("deck_title", "NotebookLM Deck")),
            "deck_summary": str(payload.get("deck_summary", "")),
            "slides": slides,
            "asset_requests": payload.get("asset_requests", []),
        }
