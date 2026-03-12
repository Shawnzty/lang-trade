"""Command-line interface."""

from __future__ import annotations

import argparse
import json

from .config import apply_cli_overrides, load_config
from .exceptions import PipelineError
from .pipeline.orchestrator import PipelineOrchestrator
from .stages import build_default_stages


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""
    parser = argparse.ArgumentParser(
        prog="app",
        description="Local-first resumable pipeline that turns a YouTube video into a narrated slide video.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="Path to config YAML.")
    common.add_argument("--env-file", help="Optional .env file for secret expansion.")
    common.add_argument("--runs-root", help="Override the configured runs root.")
    common.add_argument("--reference-audio", help="Override the configured TTS reference audio path.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    new_run = subparsers.add_parser("new-run", parents=[common], help="Create a new run scaffold.")
    new_run.add_argument("--run-id", help="Optional explicit run id.")
    new_run.add_argument("--youtube-url", help="YouTube URL to store in the run manifest.")
    new_run.add_argument("--local-video", help="Local video path to store in the run manifest.")
    new_run.add_argument("--skip-download", action="store_true", help="Skip yt-dlp download in stage 01.")

    start = subparsers.add_parser("start", parents=[common], help="Create a run and execute the full pipeline.")
    start.add_argument("--run-id", help="Optional explicit run id.")
    start.add_argument("--youtube-url", help="YouTube URL to process.")
    start.add_argument("--local-video", help="Local video to use instead of downloading.")
    start.add_argument("--skip-download", action="store_true", help="Skip yt-dlp download in stage 01.")

    run_stage = subparsers.add_parser("run-stage", parents=[common], help="Run one stage.")
    run_stage.add_argument("--run-id", required=True, help="Run id.")
    run_stage.add_argument("--stage", required=True, help="Stage id or prefix.")
    run_stage.add_argument("--force", action="store_true", help="Force rerun the stage.")

    resume = subparsers.add_parser("resume", parents=[common], help="Resume an interrupted run.")
    resume.add_argument("--run-id", required=True, help="Run id.")

    rerun = subparsers.add_parser("rerun", parents=[common], help="Rerun from one stage onward.")
    rerun.add_argument("--run-id", required=True, help="Run id.")
    rerun.add_argument("--from-stage", required=True, help="Stage id or prefix.")
    rerun.add_argument("--to-stage", help="Optional end stage.")

    status = subparsers.add_parser("status", parents=[common], help="Show run status.")
    status.add_argument("--run-id", required=True, help="Run id.")
    status.add_argument("--json", action="store_true", help="Print JSON.")

    inspect = subparsers.add_parser("inspect", parents=[common], help="Inspect one stage.")
    inspect.add_argument("--run-id", required=True, help="Run id.")
    inspect.add_argument("--stage", required=True, help="Stage id or prefix.")

    export = subparsers.add_parser("export", parents=[common], help="Run export stage.")
    export.add_argument("--run-id", required=True, help="Run id.")

    subparsers.add_parser("list-stages", parents=[common], help="List the configured stage ids.")
    return parser


def _load_orchestrator(args: argparse.Namespace) -> PipelineOrchestrator:
    config = load_config(getattr(args, "config", None), env_path=getattr(args, "env_file", None))
    config = apply_cli_overrides(
        config,
        runs_root=getattr(args, "runs_root", None),
        reference_audio_path=getattr(args, "reference_audio", None),
    )
    return PipelineOrchestrator(config, build_default_stages())


def _print_status(summary: list[dict[str, object]], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(summary, indent=2))
        return
    header = f"{'stage':38} {'state':12} {'attempt':7} error"
    print(header)
    print("-" * len(header))
    for item in summary:
        print(f"{item['stage_id']:38} {str(item['state']):12} {str(item['attempt']):7} {item.get('last_error') or ''}")


def main() -> None:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        orchestrator = _load_orchestrator(args)
        if args.command == "list-stages":
            for stage_id in orchestrator.stage_ids:
                print(stage_id)
            return
        if args.command == "new-run":
            orchestrator = PipelineOrchestrator(
                apply_cli_overrides(
                    load_config(args.config, env_path=args.env_file),
                    youtube_url=args.youtube_url,
                    local_video=args.local_video,
                    skip_download=args.skip_download or None,
                    runs_root=args.runs_root,
                    reference_audio_path=args.reference_audio,
                ),
                build_default_stages(),
            )
            run_dir = orchestrator.create_run(
                run_id=args.run_id,
                youtube_url=args.youtube_url,
                local_video=args.local_video,
            )
            print(run_dir)
            return
        if args.command == "start":
            orchestrator = PipelineOrchestrator(
                apply_cli_overrides(
                    load_config(args.config, env_path=args.env_file),
                    youtube_url=args.youtube_url,
                    local_video=args.local_video,
                    skip_download=args.skip_download or None,
                    runs_root=args.runs_root,
                    reference_audio_path=args.reference_audio,
                ),
                build_default_stages(),
            )
            run_dir = orchestrator.create_run(
                run_id=args.run_id,
                youtube_url=args.youtube_url,
                local_video=args.local_video,
            )
            orchestrator.run_range(run_dir)
            print(run_dir)
            return
        if args.command == "run-stage":
            orchestrator.run_stage(args.run_id, args.stage, force=args.force)
            return
        if args.command == "resume":
            orchestrator.resume(args.run_id)
            return
        if args.command == "rerun":
            orchestrator.rerun(args.run_id, from_stage=args.from_stage, to_stage=args.to_stage)
            return
        if args.command == "status":
            _print_status(orchestrator.status_summary(args.run_id), json_output=args.json)
            return
        if args.command == "inspect":
            print(json.dumps(orchestrator.inspect_stage(args.run_id, args.stage), indent=2))
            return
        if args.command == "export":
            orchestrator.run_range(args.run_id, from_stage="11_qa_report", to_stage="12_export", force=True)
            return
        parser.error(f"Unknown command: {args.command}")
    except PipelineError as exc:
        parser.exit(1, f"error: {exc}\n")
    except Exception as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
