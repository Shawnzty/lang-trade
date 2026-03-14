"""HTTP helpers for API-backed TTS providers."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from exceptions import AdapterError
from utils import atomic_write_text


_SENSITIVE_HEADERS = {"authorization", "xi-api-key"}


@dataclass
class HttpResponse:
    """Binary HTTP response payload."""

    status_code: int
    headers: dict[str, str]
    body: bytes


def build_multipart_form_data(
    *,
    fields: list[tuple[str, str]],
    files: list[tuple[str, Path]],
) -> tuple[bytes, str, str]:
    """Encode multipart form data and return a safe log preview."""
    boundary = f"----langtrade{uuid4().hex}"
    body = bytearray()
    preview_lines = ["[multipart_fields]"]
    for name, value in fields:
        preview_lines.append(f"{name}={value}")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    preview_lines.extend(["", "[multipart_files]"])
    for field_name, file_path in files:
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        preview_lines.append(f"{field_name}={file_path.name} ({content_type}, {file_path.stat().st_size} bytes)")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}", "\n".join(preview_lines)


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout_seconds: int | float = 120,
    log_path: Path,
    request_body_preview: str | None = None,
) -> HttpResponse:
    """Send an HTTP request and log request/response details."""
    request_headers = dict(headers or {})
    request = urllib.request.Request(url, data=data, method=method.upper())
    for header_name, header_value in request_headers.items():
        request.add_header(header_name, header_value)
    body_preview = request_body_preview
    if body_preview is None:
        body_preview = _render_body_preview(data or b"", request_headers.get("Content-Type", ""))
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            payload = HttpResponse(
                status_code=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
            )
    except urllib.error.HTTPError as exc:
        error_body = exc.read()
        error_headers = dict(exc.headers.items()) if exc.headers else {}
        _write_http_log(
            log_path=log_path,
            method=method,
            url=url,
            request_headers=request_headers,
            request_body_preview=body_preview,
            response_status=exc.code,
            response_headers=error_headers,
            response_body=error_body,
            error=None,
        )
        message = _extract_error_message(error_body, error_headers.get("Content-Type", ""))
        raise AdapterError(f"HTTP {exc.code} from {url}: {message}") from exc
    except urllib.error.URLError as exc:
        _write_http_log(
            log_path=log_path,
            method=method,
            url=url,
            request_headers=request_headers,
            request_body_preview=body_preview,
            response_status=None,
            response_headers={},
            response_body=b"",
            error=str(getattr(exc, "reason", exc)),
        )
        raise AdapterError(f"Request to {url} failed: {getattr(exc, 'reason', exc)}") from exc
    _write_http_log(
        log_path=log_path,
        method=method,
        url=url,
        request_headers=request_headers,
        request_body_preview=body_preview,
        response_status=payload.status_code,
        response_headers=payload.headers,
        response_body=payload.body,
        error=None,
    )
    return payload


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout_seconds: int | float = 120,
    log_path: Path,
    request_body_preview: str | None = None,
) -> dict[str, Any]:
    """Send an HTTP request and decode a JSON object response."""
    response = request_bytes(
        url,
        method=method,
        headers=headers,
        data=data,
        timeout_seconds=timeout_seconds,
        log_path=log_path,
        request_body_preview=request_body_preview,
    )
    if not response.body:
        return {}
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AdapterError(f"Expected JSON response from {url}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Expected JSON object response from {url}")
    return payload


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        sanitized[key] = "<redacted>" if key.lower() in _SENSITIVE_HEADERS else value
    return sanitized


def _render_body_preview(body: bytes, content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if not body:
        return ""
    if normalized == "application/json" or normalized.endswith("+json") or normalized.startswith("text/"):
        return body.decode("utf-8", errors="replace")
    return f"<binary {len(body)} bytes>"


def _extract_error_message(body: bytes, content_type: str) -> str:
    preview = _render_body_preview(body, content_type)
    if not preview:
        return "empty response body"
    try:
        payload = json.loads(preview)
    except json.JSONDecodeError:
        return preview[:300]
    for key in ("detail", "message", "error"):
        if key in payload:
            return str(payload[key])
    return json.dumps(payload, sort_keys=True)[:300]


def _write_http_log(
    *,
    log_path: Path,
    method: str,
    url: str,
    request_headers: dict[str, str],
    request_body_preview: str,
    response_status: int | None,
    response_headers: dict[str, str],
    response_body: bytes,
    error: str | None,
) -> None:
    lines = [
        f"{method.upper()} {url}",
        "",
        "[request_headers]",
        json.dumps(_sanitize_headers(request_headers), indent=2, sort_keys=True),
        "",
        "[request_body]",
        request_body_preview,
        "",
    ]
    if error is not None:
        lines.extend(["[error]", error, ""])
    lines.extend(
        [
            "[response_status]",
            str(response_status) if response_status is not None else "",
            "",
            "[response_headers]",
            json.dumps(response_headers, indent=2, sort_keys=True),
            "",
            "[response_body]",
            _render_body_preview(response_body, response_headers.get("Content-Type", "")),
            "",
        ]
    )
    atomic_write_text(log_path, "\n".join(lines))
