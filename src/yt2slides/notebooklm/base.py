"""NotebookLM base types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exceptions import AdapterError


@dataclass
class NotebookLMGenerationResult:
    """Normalized NotebookLM result."""

    notebook_id: str
    source_ids: list[str]
    request: dict[str, Any]
    response: dict[str, Any]
    deck_spec: dict[str, Any]
    raw_answer: str = ""
    partial: bool = False


@dataclass
class NotebookLMAdapterError(AdapterError):
    """NotebookLM failure that preserves partial state."""

    message: str
    notebook_id: str = ""
    source_ids: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class NotebookLMAdapter(ABC):
    """Abstract NotebookLM integration."""

    @abstractmethod
    def generate(
        self,
        *,
        run_title: str,
        transcript_path: Path,
        outline_path: Path | None,
        logs_dir: Path,
        reuse_notebook_id: str = "",
    ) -> NotebookLMGenerationResult:
        """Generate a narrated deck spec."""
