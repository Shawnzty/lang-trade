"""Pipeline base types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..structured_logging import log_event
from ..utils import (
    atomic_write_json,
    copy_or_link,
    ensure_dir,
    hash_path,
    now_utc,
    safe_relpath,
)


@dataclass(frozen=True)
class StageDefinition:
    """Static stage metadata."""

    stage_id: str
    description: str
    dependencies: tuple[str, ...]
    review_notes: str


@dataclass
class ArtifactRecord:
    """Artifact metadata persisted into stage status."""

    key: str
    path: Path
    description: str = ""
    editable: bool = False
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """Outputs from a stage run."""

    artifacts: list[ArtifactRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkspaceProtocol(Protocol):
    """Workspace methods used by stages."""

    def stage_paths(self, stage_id: str) -> dict[str, Path]:
        """Return stage paths."""

    @property
    def run_manifest_path(self) -> Path:
        """Return the run manifest path."""

    @property
    def config_snapshot_path(self) -> Path:
        """Return the config snapshot path."""

    def preferred_artifact(self, stage_id: str, artifact_key: str) -> Path | None:
        """Return the preferred artifact."""

    def register_artifact(self, stage_id: str, artifact: ArtifactRecord) -> None:
        """Persist artifact metadata."""

    def log(self, stage_id: str, event: str, **payload: Any) -> None:
        """Append a structured stage log event."""

    def load_status(self, stage_id: str) -> dict[str, Any]:
        """Load the current stage status."""


@dataclass
class StageContext:
    """Per-stage runtime context."""

    workspace: WorkspaceProtocol
    definition: StageDefinition
    config: dict[str, Any]
    run_manifest: dict[str, Any]

    def __post_init__(self) -> None:
        self.paths = self.workspace.stage_paths(self.definition.stage_id)
        self.captured_inputs: list[dict[str, Any]] = []

    @property
    def stage_id(self) -> str:
        """Return the current stage id."""
        return self.definition.stage_id

    def log(self, event: str, **payload: Any) -> None:
        """Write a structured stage log."""
        self.workspace.log(self.stage_id, event, **payload)

    def ensure_output_dir(self, relative: str = "") -> Path:
        """Return an output directory path."""
        path = self.paths["outputs"] / relative
        return ensure_dir(path)

    def output_path(self, relative: str) -> Path:
        """Return an output file path."""
        path = self.paths["outputs"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def input_path(self, relative: str) -> Path:
        """Return an input file path."""
        path = self.paths["inputs"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def edit_path(self, relative: str) -> Path:
        """Return an edit path."""
        path = self.paths["edits"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def log_path(self, relative: str) -> Path:
        """Return a stage log path."""
        path = self.paths["logs"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def capture_external_input(self, source: Path, alias: str) -> Path:
        """Capture a non-stage input under the stage input directory."""
        destination = self.input_path(alias)
        copy_or_link(source, destination)
        self.captured_inputs.append(
            {
                "source_stage": None,
                "artifact_key": alias,
                "resolved_path": str(source),
                "captured_path": str(destination),
            }
        )
        return destination

    def require_input(self, stage_id: str, artifact_key: str, alias: str | None = None) -> Path:
        """Capture a required upstream preferred artifact."""
        source = self.workspace.preferred_artifact(stage_id, artifact_key)
        if source is None:
            raise FileNotFoundError(f"Missing required input {stage_id}:{artifact_key}")
        destination = self.input_path(f"{stage_id}/{alias or source.name}")
        copy_or_link(source, destination)
        self.captured_inputs.append(
            {
                "source_stage": stage_id,
                "artifact_key": artifact_key,
                "resolved_path": str(source),
                "captured_path": str(destination),
                "hash": hash_path(source),
            }
        )
        return destination

    def optional_input(self, stage_id: str, artifact_key: str, alias: str | None = None) -> Path | None:
        """Capture an optional upstream artifact."""
        source = self.workspace.preferred_artifact(stage_id, artifact_key)
        if source is None:
            return None
        destination = self.input_path(f"{stage_id}/{alias or source.name}")
        copy_or_link(source, destination)
        self.captured_inputs.append(
            {
                "source_stage": stage_id,
                "artifact_key": artifact_key,
                "resolved_path": str(source),
                "captured_path": str(destination),
                "hash": hash_path(source),
            }
        )
        return destination

    def register_artifact(self, artifact: ArtifactRecord) -> None:
        """Register one output artifact."""
        self.workspace.register_artifact(self.stage_id, artifact)

    def finalize_inputs(self) -> None:
        """Write the input manifest."""
        atomic_write_json(self.paths["inputs"] / "input_manifest.json", self.captured_inputs)


class BaseStage:
    """Base class for resumable pipeline stages."""

    definition: StageDefinition

    def validate_inputs(self, context: StageContext) -> None:
        """Validate upstream prerequisites."""

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        """Load or capture inputs."""
        return {}

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute the stage."""
        raise NotImplementedError

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        """Persist stage outputs."""
        return StageResult()

    def save_status(
        self,
        context: StageContext,
        *,
        state: str,
        attempt: int,
        started_at: str,
        finished_at: str,
        dependency_tokens: dict[str, str],
        error: dict[str, Any] | None,
        result: StageResult | None,
    ) -> dict[str, Any]:
        """Build a status payload."""
        previous = context.workspace.load_status(context.stage_id)
        status = {
            "stage_id": context.stage_id,
            "description": context.definition.description,
            "state": state,
            "attempt": attempt,
            "started_at": started_at,
            "finished_at": finished_at,
            "dependencies": list(context.definition.dependencies),
            "dependency_tokens": dependency_tokens,
            "artifacts": previous.get("artifacts", {}),
            "preferred_artifacts": previous.get("preferred_artifacts", {}),
            "inventory": previous.get(
                "inventory",
                {
                    "inputs": [],
                    "outputs": [],
                    "edits": [],
                    "logs": [],
                },
            ),
            "notes": result.notes if result else [],
            "last_error": error,
            "stale_reason": None,
            "updated_at": now_utc(),
        }
        return status

    def execute(self, context: StageContext) -> StageResult:
        """Run the full stage lifecycle."""
        self.validate_inputs(context)
        inputs = self.load_inputs(context)
        raw_result = self.run(context, inputs)
        result = self.save_outputs(context, raw_result)
        for artifact in result.artifacts:
            context.register_artifact(artifact)
        context.finalize_inputs()
        log_event(
            context.paths["logs"] / "stage.jsonl",
            "stage_complete",
            stage_id=context.stage_id,
            artifact_count=len(result.artifacts),
        )
        return result
