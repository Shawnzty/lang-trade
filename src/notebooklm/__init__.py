"""NotebookLM adapters."""

from .base import NotebookLMAdapter, NotebookLMAdapterError, NotebookLMGenerationResult
from .notebooklm_mcp_cli_adapter import NotebookLMMcpCliAdapter

__all__ = [
    "NotebookLMAdapter",
    "NotebookLMAdapterError",
    "NotebookLMGenerationResult",
    "NotebookLMMcpCliAdapter",
]
