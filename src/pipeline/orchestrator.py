"""Pipeline orchestration."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from exceptions import PipelineError
from utils import atomic_write_json, now_utc, read_json, slugify
from .base import BaseStage, StageContext
from .workspace import DEFAULT_STAGE_STATE, RunWorkspace


class PipelineOrchestrator:
    """Entry point for run creation, execution, and status inspection."""

    def __init__(self, config: dict[str, Any], stages: list[BaseStage]) -> None:
        self.config = config
        self.stages = stages
        self.stage_map = {stage.definition.stage_id: stage for stage in stages}
        self.stage_ids = [stage.definition.stage_id for stage in stages]

    def normalize_stage_id(self, value: str) -> str:
        """Accept a full stage id or prefix."""
        if value in self.stage_map:
            return value
        matches = [stage_id for stage_id in self.stage_ids if stage_id.startswith(value)]
        if len(matches) == 1:
            return matches[0]
        raise PipelineError(f"Unknown stage: {value}")

    def create_workspace(self, run_dir: Path) -> RunWorkspace:
        """Build a workspace wrapper."""
        return RunWorkspace(run_dir, [stage.definition for stage in self.stages])

    def create_run(
        self,
        *,
        run_id: str | None = None,
        youtube_url: str | None = None,
        local_video: str | None = None,
    ) -> Path:
        """Create a new run directory and scaffold stages."""
        runs_root = Path(self.config["runs_root"])
        runs_root.mkdir(parents=True, exist_ok=True)
        title = youtube_url or local_video or self.config["source"].get("title") or "run"
        generated_id = f"{now_utc().replace(':', '').replace('+00:00', 'Z')}-{slugify(title)[:48]}"
        final_run_id = run_id or generated_id
        run_dir = runs_root / final_run_id
        if run_dir.exists():
            raise PipelineError(f"Run directory already exists: {run_dir}")
        workspace = self.create_workspace(run_dir)
        workspace.ensure_structure()
        run_manifest = {
            "run_id": final_run_id,
            "created_at": now_utc(),
            "workspace_root": str(Path(self.config["workspace_root"]).resolve()),
            "runs_root": str(runs_root.resolve()),
            "source": {
                "youtube_url": youtube_url or self.config["source"].get("youtube_url", ""),
                "local_video": local_video or self.config["source"].get("local_video", ""),
                "skip_download": bool(self.config["source"].get("skip_download", False)),
            },
            "stage_order": self.stage_ids,
            "app": "lang-trade",
            "version": "0.2.0",
        }
        atomic_write_json(workspace.run_manifest_path, run_manifest)
        atomic_write_json(workspace.config_snapshot_path, self.config)
        self.run_stage(final_run_id, "00_init_run", force=True)
        return run_dir

    def load_workspace(self, run_id_or_path: str | Path) -> tuple[RunWorkspace, dict[str, Any]]:
        """Load a run workspace by run id or path."""
        run_path = Path(run_id_or_path)
        if not run_path.exists():
            run_path = Path(self.config["runs_root"]) / str(run_id_or_path)
        if not run_path.exists() or not run_path.is_dir():
            raise PipelineError(f"Run directory does not exist: {run_path}")
        workspace = self.create_workspace(run_path)
        workspace.ensure_structure()
        workspace.require_initialized()
        workspace.mark_stale()
        return workspace, read_json(workspace.run_manifest_path, {})

    def run_stage(self, run_id_or_path: str | Path, stage_id: str, *, force: bool = False) -> None:
        """Run one stage."""
        normalized = self.normalize_stage_id(stage_id)
        workspace, run_manifest = self.load_workspace(run_id_or_path)
        stage = self.stage_map[normalized]
        status = workspace.load_status(normalized)
        if status.get("state") == "completed" and not force:
            return
        definition = stage.definition
        dependency_tokens = {
            dependency: workspace.current_stage_token(dependency) for dependency in definition.dependencies
        }
        attempt = int(status.get("attempt", 0)) + 1
        started_at = now_utc()
        running_status = stage.save_status(
            StageContext(workspace, definition, self.config, run_manifest),
            state="running",
            attempt=attempt,
            started_at=started_at,
            finished_at="",
            dependency_tokens=dependency_tokens,
            error=None,
            result=None,
        )
        workspace.save_status(normalized, running_status)
        context = StageContext(workspace, definition, self.config, run_manifest)
        with workspace.stage_lock(normalized):
            try:
                result = stage.execute(context)
                final_status = stage.save_status(
                    context,
                    state="completed",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=now_utc(),
                    dependency_tokens=dependency_tokens,
                    error=None,
                    result=result,
                )
                workspace.save_status(normalized, final_status)
            except Exception as exc:
                context.finalize_inputs()
                failed_status = stage.save_status(
                    context,
                    state="failed",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=now_utc(),
                    dependency_tokens=dependency_tokens,
                    error={
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    result=None,
                )
                workspace.save_status(normalized, failed_status)
                workspace.log(normalized, "stage_failed", error=str(exc))
                workspace.mark_stale()
                raise
        workspace.mark_stale()

    def run_range(
        self,
        run_id_or_path: str | Path,
        *,
        from_stage: str | None = None,
        to_stage: str | None = None,
        force: bool = False,
    ) -> None:
        """Run a contiguous range of stages."""
        start_stage = self.normalize_stage_id(from_stage) if from_stage else self.stage_ids[1]
        end_stage = self.normalize_stage_id(to_stage) if to_stage else self.stage_ids[-1]
        start_index = self.stage_ids.index(start_stage)
        end_index = self.stage_ids.index(end_stage)
        if start_index > end_index:
            raise PipelineError("from_stage must be before to_stage")
        for stage_id in self.stage_ids[start_index : end_index + 1]:
            workspace, _ = self.load_workspace(run_id_or_path)
            status = workspace.load_status(stage_id)
            if not force and status.get("state") == "completed":
                continue
            self.run_stage(
                run_id_or_path,
                stage_id,
                force=force or status.get("state") in {"stale", "failed", DEFAULT_STAGE_STATE},
            )

    def resume(self, run_id_or_path: str | Path) -> None:
        """Resume a run from the first incomplete stage."""
        self.run_range(run_id_or_path)

    def rerun(self, run_id_or_path: str | Path, *, from_stage: str, to_stage: str | None = None) -> None:
        """Rerun a stage range from a specific stage."""
        self.run_range(run_id_or_path, from_stage=from_stage, to_stage=to_stage, force=True)

    def status_summary(self, run_id_or_path: str | Path) -> list[dict[str, Any]]:
        """Return a summary of stage statuses."""
        workspace, _ = self.load_workspace(run_id_or_path)
        summary: list[dict[str, Any]] = []
        for stage_id in self.stage_ids:
            status = workspace.refresh_status(stage_id)
            summary.append(
                {
                    "stage_id": stage_id,
                    "state": status.get("state"),
                    "attempt": status.get("attempt"),
                    "started_at": status.get("started_at"),
                    "finished_at": status.get("finished_at"),
                    "stale_reason": status.get("stale_reason"),
                    "artifacts": status.get("preferred_artifacts", {}),
                    "last_error": status.get("last_error", {}).get("message")
                    if isinstance(status.get("last_error"), dict)
                    else None,
                }
            )
        return summary

    def inspect_stage(self, run_id_or_path: str | Path, stage_id: str) -> dict[str, Any]:
        """Return full stage inspection details."""
        normalized = self.normalize_stage_id(stage_id)
        workspace, run_manifest = self.load_workspace(run_id_or_path)
        return {
            "run_manifest": run_manifest,
            "stage_status": workspace.refresh_status(normalized),
            "stage_paths": {key: str(value) for key, value in workspace.stage_paths(normalized).items()},
        }
