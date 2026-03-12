"""Shared utilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .exceptions import PipelineError


class CommandError(PipelineError):
    """Raised when an external command fails."""


def now_utc() -> str:
    """Return an ISO timestamp in UTC."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    """Create a directory if needed."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_relpath(path: Path, root: Path) -> str:
    """Return a stable POSIX relative path."""
    return path.relative_to(root).as_posix()


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically."""
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_text(path: Path, text: str) -> None:
    """Append UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSON line."""
    append_text(path, json.dumps(payload, sort_keys=True) + "\n")


def read_json(path: Path, default: Any | None = None) -> Any:
    """Read JSON or return a default."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path, default: str = "") -> str:
    """Read UTF-8 text or return a default."""
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def copy_file(source: Path, destination: Path) -> Path:
    """Copy one file idempotently."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def copy_tree(source: Path, destination: Path) -> Path:
    """Copy a directory tree idempotently."""
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination


def copy_or_link(source: Path, destination: Path) -> Path:
    """Create a symlink when possible, otherwise copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    try:
        if source.is_dir():
            os.symlink(source, destination, target_is_directory=True)
        else:
            os.symlink(source, destination)
    except OSError:
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    return destination


def require_binary(binary: str) -> str:
    """Resolve a binary or fail clearly."""
    resolved = shutil.which(binary)
    if resolved is None:
        raise PipelineError(f"Required binary not found on PATH: {binary}")
    return resolved


def slugify(value: str, *, fallback: str = "run") -> str:
    """Convert a string into a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or fallback


def hash_bytes(payload: bytes) -> str:
    """Hash raw bytes."""
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    """Hash a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_path(path: Path) -> str | None:
    """Hash a file or directory tree."""
    if not path.exists():
        return None
    if path.is_file():
        return hash_file(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(safe_relpath(child, path).encode("utf-8"))
        digest.update(hash_file(child).encode("utf-8"))
    return digest.hexdigest()


def hash_payload(payload: Any) -> str:
    """Hash a JSON-serializable payload deterministically."""
    normalized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def command_to_string(args: list[str]) -> str:
    """Render a shell-safe command string."""
    return shlex.join(args)


def run_command(
    args: list[str],
    *,
    log_path: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and persist a detailed command log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        input=input_text,
        capture_output=True,
        check=False,
        text=True,
    )
    atomic_write_text(
        log_path,
        "\n".join(
            [
                f"$ {command_to_string(args)}",
                "",
                f"[exit_code] {process.returncode}",
                "",
                "[stdout]",
                process.stdout,
                "",
                "[stderr]",
                process.stderr,
                "",
            ]
        ),
    )
    if check and process.returncode != 0:
        raise CommandError(
            f"Command failed with exit code {process.returncode}: {command_to_string(args)}"
        )
    return process


def stage_prefix(stage_id: str) -> str:
    """Return the numeric prefix for a stage."""
    return stage_id.split("_", 1)[0]


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a text blob."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def load_dotenv(path: Path | None) -> dict[str, str]:
    """Load a minimal .env file."""
    values: dict[str, str] = {}
    if path is None or not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def expand_env_values(payload: Any, env: dict[str, str]) -> Any:
    """Expand ${VAR} references recursively."""
    if isinstance(payload, dict):
        return {key: expand_env_values(value, env) for key, value in payload.items()}
    if isinstance(payload, list):
        return [expand_env_values(item, env) for item in payload]
    if isinstance(payload, str):
        def replace(match: re.Match[str]) -> str:
            return env.get(match.group(1), match.group(0))
        return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, payload)
    return payload


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    """Split a list into fixed-size chunks."""
    return [items[index : index + size] for index in range(0, len(items), max(size, 1))]


def estimate_seconds_from_text(text: str, words_per_minute: int = 145) -> float:
    """Estimate narration duration from text."""
    word_count = len(re.findall(r"\S+", text))
    if word_count == 0:
        return 1.0
    return max(1.0, word_count / max(words_per_minute, 60) * 60.0)


def wrap_text(text: str, width: int = 88) -> str:
    """Wrap paragraphs while preserving blank lines."""
    parts: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            parts.append("")
            continue
        parts.append(textwrap.fill(paragraph, width=width))
    return "\n".join(parts)
