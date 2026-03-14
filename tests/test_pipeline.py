from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import DEFAULT_CONFIG, deep_merge
from notebooklm import NotebookLMAdapterError, NotebookLMGenerationResult
from pipeline.base import ArtifactRecord, BaseStage, StageContext, StageDefinition, StageResult
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.workspace import RunWorkspace
from stages import build_default_stages
import stages.pipeline_stages as stage_module


def make_config(tmp_path: Path) -> dict[str, Any]:
    return deep_merge(
        DEFAULT_CONFIG,
        {
            "workspace_root": str(tmp_path / "workspace"),
            "runs_root": str(tmp_path / "workspace" / "runs"),
            "tts": {
                "reference_audio_path": str(tmp_path / "voice.wav"),
            },
        },
    )


class DummyInitStage(BaseStage):
    definition = StageDefinition(
        "00_init_run",
        "Initialize the dummy run.",
        (),
        "No manual review.",
    )

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        marker = context.output_path("init.json")
        marker.write_text('{"ok": true}\n', encoding="utf-8")
        return {"marker": marker}

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(artifacts=[ArtifactRecord("init_marker", result["marker"], "Dummy init marker.")])


class DummyArtifactStage(BaseStage):
    def __init__(self, stage_id: str, dependencies: tuple[str, ...], output_name: str, *, editable: bool = False) -> None:
        self.definition = StageDefinition(stage_id, f"Dummy {stage_id}", dependencies, "No manual review.")
        self.output_name = output_name
        self.editable = editable

    def load_inputs(self, context: StageContext) -> dict[str, Any]:
        captured: dict[str, Any] = {}
        for dependency in self.definition.dependencies:
            dependency_key = "init_marker" if dependency == "00_init_run" else "artifact"
            captured[dependency] = context.require_input(dependency, dependency_key)
        return captured

    def run(self, context: StageContext, inputs: dict[str, Any]) -> dict[str, Any]:
        artifact_path = context.output_path(self.output_name)
        text = f"{context.stage_id}\n"
        for dependency, path in inputs.items():
            text += f"{dependency}:{Path(path).read_text(encoding='utf-8')}"
        artifact_path.write_text(text, encoding="utf-8")
        return {"artifact": artifact_path}

    def save_outputs(self, context: StageContext, result: dict[str, Any]) -> StageResult:
        return StageResult(
            artifacts=[
                ArtifactRecord(
                    "artifact",
                    result["artifact"],
                    f"Artifact for {context.stage_id}.",
                    editable=self.editable,
                )
            ]
        )


def make_dummy_orchestrator(tmp_path: Path) -> PipelineOrchestrator:
    config = make_config(tmp_path)
    stages = [
        DummyInitStage(),
        DummyArtifactStage("01_first", ("00_init_run",), "artifact.txt", editable=True),
        DummyArtifactStage("02_second", ("01_first",), "artifact.txt"),
    ]
    return PipelineOrchestrator(config, stages)


def register_upstream_artifact(
    workspace: RunWorkspace,
    *,
    stage_id: str,
    artifact_key: str,
    file_name: str,
    content: str,
    editable: bool = False,
) -> Path:
    output_path = workspace.stage_paths(stage_id)["outputs"] / file_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    workspace.register_artifact(stage_id, ArtifactRecord(artifact_key, output_path, editable=editable))
    status = workspace.load_status(stage_id)
    status["state"] = "completed"
    status["dependency_tokens"] = {
        dependency: workspace.current_stage_token(dependency)
        for dependency in workspace.stage_map[stage_id].dependencies
    }
    workspace.save_status(stage_id, status)
    return output_path


def test_create_run_scaffolds_all_stage_directories(tmp_path: Path) -> None:
    orchestrator = PipelineOrchestrator(make_config(tmp_path), build_default_stages())
    run_dir = orchestrator.create_run(youtube_url="https://example.com/watch?v=test")

    assert (run_dir / "run_manifest.json").exists()
    assert (run_dir / "config.snapshot.json").exists()

    for stage in build_default_stages():
        stage_dir = run_dir / stage.definition.stage_id
        assert (stage_dir / "inputs").exists()
        assert (stage_dir / "outputs").exists()
        assert (stage_dir / "edits").exists()
        assert (stage_dir / "logs").exists()
        assert (stage_dir / "status.json").exists()
        assert (stage_dir / "README.md").exists()


def test_stage_orchestration_and_resume_logic(tmp_path: Path) -> None:
    orchestrator = make_dummy_orchestrator(tmp_path)
    run_dir = orchestrator.create_run()
    run_id = run_dir.name

    orchestrator.run_stage(run_id, "01_first")
    workspace, _ = orchestrator.load_workspace(run_id)
    assert workspace.load_status("01_first")["state"] == "completed"
    assert workspace.load_status("02_second")["state"] == "not_started"

    orchestrator.resume(run_id)
    workspace, _ = orchestrator.load_workspace(run_id)
    assert workspace.load_status("02_second")["state"] == "completed"


def test_edited_artifact_marks_downstream_stage_stale_and_is_preferred(tmp_path: Path) -> None:
    orchestrator = make_dummy_orchestrator(tmp_path)
    run_dir = orchestrator.create_run()
    run_id = run_dir.name

    orchestrator.run_range(run_id)
    workspace, _ = orchestrator.load_workspace(run_id)
    stage_one_output = workspace.stage_paths("01_first")["outputs"] / "artifact.txt"
    edited = workspace.stage_paths("01_first")["edits"] / "artifact.txt"
    edited.write_text("edited-version\n", encoding="utf-8")

    workspace.mark_stale()
    stage_two_status = workspace.load_status("02_second")
    assert stage_two_status["state"] == "stale"
    assert workspace.preferred_artifact("01_first", "artifact") == edited

    orchestrator.run_stage(run_id, "02_second", force=True)
    downstream_input = workspace.stage_paths("02_second")["inputs"] / "01_first" / "artifact.txt"
    assert downstream_input.read_text(encoding="utf-8") == "edited-version\n"


def test_manifest_generation_and_status_artifacts(tmp_path: Path) -> None:
    orchestrator = make_dummy_orchestrator(tmp_path)
    run_dir = orchestrator.create_run()
    run_id = run_dir.name

    orchestrator.run_stage(run_id, "01_first")
    workspace, _ = orchestrator.load_workspace(run_id)
    status = workspace.refresh_status("01_first")

    assert "artifact" in status["artifacts"]
    assert status["preferred_artifacts"]["artifact"]["path"] == "outputs/artifact.txt"
    assert (workspace.stage_paths("01_first")["inputs"] / "input_manifest.json").exists()


def test_notebooklm_stage_is_mockable_and_writes_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(tmp_path)
    orchestrator = PipelineOrchestrator(config, build_default_stages())
    run_dir = orchestrator.create_run(youtube_url="https://example.com/watch?v=test")
    workspace = RunWorkspace(run_dir, [stage.definition for stage in build_default_stages()])

    register_upstream_artifact(
        workspace,
        stage_id="03_transcribe",
        artifact_key="transcript_clean",
        file_name="transcript_clean.md",
        content="# Transcript\n\nExample transcript.\n",
        editable=True,
    )
    register_upstream_artifact(
        workspace,
        stage_id="04_structure_content",
        artifact_key="outline_md",
        file_name="outline.md",
        content="# Outline\n\n- Example\n",
        editable=True,
    )
    register_upstream_artifact(
        workspace,
        stage_id="04_structure_content",
        artifact_key="outline_json",
        file_name="outline.json",
        content=json.dumps(
            {
                "title": "Outline",
                "candidate_slide_boundaries": [
                    {"slide_number": 1, "title": "Intro", "objective": "Intro", "bullets": ["A", "B"]}
                ],
            }
        ),
        editable=False,
    )

    class FakeNotebookAdapter:
        def generate(self, **_: Any) -> NotebookLMGenerationResult:
            return NotebookLMGenerationResult(
                notebook_id="nb-123",
                source_ids=["source-1"],
                request={"prompt": "test"},
                response={"answer": '{"deck_title": "Deck", "deck_summary": "Summary", "slides": [], "asset_requests": []}'},
                deck_spec={
                    "deck_title": "Deck",
                    "deck_summary": "Summary",
                    "slides": [
                        {
                            "slide_number": 1,
                            "title": "Intro",
                            "objective": "Open the talk",
                            "on_slide_text": "Intro",
                            "bullets": ["Point A"],
                            "suggested_visual": "Diagram",
                            "speaker_notes": "Notes",
                            "narration_text": "Narration",
                            "estimated_duration_sec": 12.0,
                        }
                    ],
                    "asset_requests": [{"slide_number": 1, "request": "Diagram", "priority": "high"}],
                },
            )

    monkeypatch.setattr(stage_module, "_notebooklm_adapter", lambda config: FakeNotebookAdapter())
    orchestrator.run_stage(run_dir, "05_generate_deck_with_notebooklm", force=True)

    stage_dir = run_dir / "05_generate_deck_with_notebooklm" / "outputs"
    assert (stage_dir / "deck_spec.json").exists()
    assert (stage_dir / "slide_titles.md").exists()
    assert (stage_dir / "slide_content.md").exists()
    assert (stage_dir / "speaker_notes.md").exists()
    assert (stage_dir / "narration_script.md").exists()
    assert (stage_dir / "asset_requests.json").exists()


def test_notebooklm_stage_falls_back_gracefully_on_adapter_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config(tmp_path)
    orchestrator = PipelineOrchestrator(config, build_default_stages())
    run_dir = orchestrator.create_run(youtube_url="https://example.com/watch?v=test")
    workspace = RunWorkspace(run_dir, [stage.definition for stage in build_default_stages()])

    register_upstream_artifact(
        workspace,
        stage_id="03_transcribe",
        artifact_key="transcript_clean",
        file_name="transcript_clean.md",
        content="# Transcript\n\nExample transcript.\n",
        editable=True,
    )
    register_upstream_artifact(
        workspace,
        stage_id="04_structure_content",
        artifact_key="outline_json",
        file_name="outline.json",
        content=json.dumps(
            {
                "title": "Outline",
                "candidate_slide_boundaries": [
                    {"slide_number": 1, "title": "Intro", "objective": "Intro", "bullets": ["A", "B"]}
                ],
            }
        ),
        editable=False,
    )

    class BrokenNotebookAdapter:
        def generate(self, **_: Any) -> NotebookLMGenerationResult:
            raise NotebookLMAdapterError("NotebookLM unavailable", notebook_id="nb-partial", source_ids=["source-partial"])

    monkeypatch.setattr(stage_module, "_notebooklm_adapter", lambda config: BrokenNotebookAdapter())
    orchestrator.run_stage(run_dir, "05_generate_deck_with_notebooklm", force=True)

    stage_dir = run_dir / "05_generate_deck_with_notebooklm" / "outputs"
    assert (stage_dir / "manual_recovery.md").exists()
    assert (stage_dir / "deck_spec.json").exists()
    status = workspace.refresh_status("05_generate_deck_with_notebooklm")
    assert status["state"] == "completed"
    assert status["notes"]
