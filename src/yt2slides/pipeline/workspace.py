"""Run workspace management."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..exceptions import PipelineError, StageLockError
from ..structured_logging import log_event
from ..utils import (
    atomic_write_json,
    atomic_write_text,
    ensure_dir,
    hash_path,
    hash_payload,
    now_utc,
    read_json,
    safe_relpath,
)
from .base import ArtifactRecord, StageDefinition


DEFAULT_STAGE_STATE = "not_started"


class StageLock:
    """Simple file lock using O_EXCL semantics."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.fd: int | None = None

    def __enter__(self) -> "StageLock":
        ensure_dir(self.lock_path.parent)
        try:
            self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise StageLockError(f"Stage is locked: {self.lock_path}") from exc
        os.write(self.fd, now_utc().encode("utf-8"))
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.lock_path.exists():
            self.lock_path.unlink()


class RunWorkspace:
    """Owns the run directory and stage status persistence."""

    def __init__(self, run_dir: Path, stage_definitions: list[StageDefinition]) -> None:
        self.run_dir = run_dir
        self.stage_definitions = stage_definitions
        self.stage_map = {definition.stage_id: definition for definition in stage_definitions}

    @property
    def run_manifest_path(self) -> Path:
        return self.run_dir / "run_manifest.json"

    @property
    def config_snapshot_path(self) -> Path:
        return self.run_dir / "config.snapshot.json"

    def stage_dir(self, stage_id: str) -> Path:
        """Return the stage directory."""
        return self.run_dir / stage_id

    def stage_paths(self, stage_id: str) -> dict[str, Path]:
        """Return all standard paths for a stage."""
        stage_root = self.stage_dir(stage_id)
        return {
            "root": stage_root,
            "inputs": stage_root / "inputs",
            "outputs": stage_root / "outputs",
            "edits": stage_root / "edits",
            "logs": stage_root / "logs",
            "status": stage_root / "status.json",
            "readme": stage_root / "README.md",
            "lock": stage_root / ".lock",
        }

    def default_status(self, stage_id: str) -> dict[str, Any]:
        """Return the initial stage status."""
        definition = self.stage_map[stage_id]
        return {
            "stage_id": stage_id,
            "description": definition.description,
            "state": DEFAULT_STAGE_STATE,
            "attempt": 0,
            "started_at": None,
            "finished_at": None,
            "dependencies": list(definition.dependencies),
            "dependency_tokens": {},
            "artifacts": {},
            "preferred_artifacts": {},
            "inventory": {
                "inputs": [],
                "outputs": [],
                "edits": [],
                "logs": [],
            },
            "notes": [],
            "last_error": None,
            "stale_reason": None,
            "updated_at": now_utc(),
        }

    def render_stage_readme(self, stage_id: str) -> str:
        """Render the standard stage README."""
        definition = self.stage_map[stage_id]
        dependency_text = ", ".join(definition.dependencies) if definition.dependencies else "none"
        return (
            f"# {stage_id}\n\n"
            f"{definition.description}\n\n"
            "## Manual review\n\n"
            f"{definition.review_notes}\n\n"
            "## Directory contract\n\n"
            "- `inputs/` captures the exact upstream artifacts used by this run.\n"
            "- `outputs/` contains generated artifacts.\n"
            "- `edits/` contains manual overrides preferred by downstream stages.\n"
            "- `logs/` stores structured stage logs and command logs.\n"
            "- `status.json` stores resumability, preferred artifacts, hashes, and stale state.\n\n"
            "## Dependencies\n\n"
            f"- {dependency_text}\n"
        )

    def ensure_structure(self) -> None:
        """Create the run and stage directory layout."""
        ensure_dir(self.run_dir)
        for definition in self.stage_definitions:
            self.ensure_stage(definition.stage_id)

    def ensure_stage(self, stage_id: str) -> None:
        """Create one stage scaffold."""
        paths = self.stage_paths(stage_id)
        for key in ("root", "inputs", "outputs", "edits", "logs"):
            ensure_dir(paths[key])
        if not paths["status"].exists():
            atomic_write_json(paths["status"], self.default_status(stage_id))
        if not paths["readme"].exists():
            atomic_write_text(paths["readme"], self.render_stage_readme(stage_id))

    def load_status(self, stage_id: str) -> dict[str, Any]:
        """Load a stage status file."""
        return read_json(self.stage_paths(stage_id)["status"], self.default_status(stage_id))

    def save_status(self, stage_id: str, status: dict[str, Any]) -> None:
        """Write a stage status file."""
        status["updated_at"] = now_utc()
        atomic_write_json(self.stage_paths(stage_id)["status"], status)

    def stage_lock(self, stage_id: str) -> StageLock:
        """Return a context manager for the stage lock."""
        return StageLock(self.stage_paths(stage_id)["lock"])

    def log(self, stage_id: str, event: str, **payload: Any) -> None:
        """Write one structured log line."""
        log_event(self.stage_paths(stage_id)["logs"] / "stage.jsonl", event, stage_id=stage_id, **payload)

    def register_artifact(self, stage_id: str, artifact: ArtifactRecord) -> None:
        """Persist one stage artifact record."""
        status = self.load_status(stage_id)
        status.setdefault("artifacts", {})[artifact.key] = {
            "path": safe_relpath(artifact.path, self.stage_dir(stage_id)),
            "editable": artifact.editable,
            "description": artifact.description,
            "provenance": artifact.provenance,
            "kind": "dir" if artifact.path.is_dir() else "file",
        }
        self.save_status(stage_id, status)
        self.refresh_status(stage_id)

    def _inventory(self, root: Path) -> list[str]:
        """Return a stable tree inventory."""
        if not root.exists():
            return []
        items: list[str] = []
        for item in sorted(root.rglob("*")):
            name = item.relative_to(root).as_posix()
            if item.is_dir():
                name += "/"
            items.append(name)
        return items

    def edit_override_for(self, stage_id: str, relative_output_path: str) -> Path | None:
        """Return an edit override for an output artifact."""
        edits_dir = self.stage_paths(stage_id)["edits"]
        exact = edits_dir / relative_output_path
        if exact.exists():
            return exact
        basename_match = edits_dir / Path(relative_output_path).name
        if basename_match.exists():
            return basename_match
        return None

    def preferred_artifact(self, stage_id: str, artifact_key: str) -> Path | None:
        """Return the preferred artifact, favoring edits."""
        status = self.load_status(stage_id)
        artifact = status.get("artifacts", {}).get(artifact_key)
        if artifact is None:
            return None
        relative_path = artifact["path"]
        if artifact.get("editable"):
            edited = self.edit_override_for(stage_id, relative_path)
            if edited is not None:
                return edited
        output = self.stage_dir(stage_id) / relative_path
        return output if output.exists() else None

    def artifact_hashes(self, stage_id: str) -> dict[str, str | None]:
        """Return hashes for all current preferred artifacts."""
        status = self.load_status(stage_id)
        hashes: dict[str, str | None] = {}
        for artifact_key in status.get("artifacts", {}):
            preferred = self.preferred_artifact(stage_id, artifact_key)
            hashes[artifact_key] = hash_path(preferred) if preferred else None
        return hashes

    def current_stage_token(self, stage_id: str, dependency_tokens: dict[str, str] | None = None) -> str:
        """Compute the current stage token."""
        status = self.load_status(stage_id)
        definition = self.stage_map[stage_id]
        payload = {
            "stage_id": stage_id,
            "state": status.get("state"),
            "dependencies": dependency_tokens
            or {dependency: self.current_stage_token(dependency) for dependency in definition.dependencies},
            "artifacts": self.artifact_hashes(stage_id),
        }
        return hash_payload(payload)

    def refresh_status(self, stage_id: str) -> dict[str, Any]:
        """Reindex one stage status file from disk."""
        status = self.load_status(stage_id)
        paths = self.stage_paths(stage_id)
        preferred: dict[str, Any] = {}
        for artifact_key, metadata in status.get("artifacts", {}).items():
            artifact_path = self.preferred_artifact(stage_id, artifact_key)
            preferred[artifact_key] = {
                "path": safe_relpath(artifact_path, self.stage_dir(stage_id)) if artifact_path else None,
                "hash": hash_path(artifact_path) if artifact_path else None,
                "source": (
                    "edits"
                    if artifact_path and str(artifact_path).startswith(str(paths["edits"]))
                    else "outputs"
                    if artifact_path
                    else None
                ),
                "editable": metadata.get("editable", False),
                "description": metadata.get("description", ""),
            }
        status["preferred_artifacts"] = preferred
        status["inventory"] = {
            "inputs": self._inventory(paths["inputs"]),
            "outputs": self._inventory(paths["outputs"]),
            "edits": self._inventory(paths["edits"]),
            "logs": self._inventory(paths["logs"]),
        }
        self.save_status(stage_id, status)
        return status

    def mark_stale(self) -> None:
        """Mark downstream stages stale when dependency tokens change."""
        tokens: dict[str, str] = {}
        for definition in self.stage_definitions:
            stage_id = definition.stage_id
            self.refresh_status(stage_id)
            status = self.load_status(stage_id)
            dependency_tokens = {dependency: tokens[dependency] for dependency in definition.dependencies}
            saved = status.get("dependency_tokens", {})
            if status.get("state") not in {DEFAULT_STAGE_STATE, "running"} and saved and saved != dependency_tokens:
                status["state"] = "stale"
                status["stale_reason"] = "Upstream preferred artifact changed."
                self.save_status(stage_id, status)
            tokens[stage_id] = self.current_stage_token(stage_id, dependency_tokens)

    def require_initialized(self) -> None:
        """Ensure the run looks initialized."""
        missing: list[str] = []
        if not self.run_manifest_path.exists():
            missing.append("run_manifest.json")
        if not self.config_snapshot_path.exists():
            missing.append("config.snapshot.json")
        if missing:
            raise PipelineError(
                f"Run directory is not initialized: {self.run_dir} (missing {', '.join(missing)})"
            )
