"""Shared exceptions."""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Raised for user-facing pipeline failures."""


class AdapterError(RuntimeError):
    """Raised when an external integration fails."""


class AdapterUnavailableError(AdapterError):
    """Raised when a required external dependency is missing."""


class StageLockError(PipelineError):
    """Raised when a stage lock cannot be acquired."""
