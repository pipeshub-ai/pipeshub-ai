"""Parse KB upload SSE (`text/event-stream`) responses for integration tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import requests

from pipeshub_client import PipeshubAuthError, PipeshubClientError

_SSEEnvelope = dict[str, str]


def iter_sse_envelopes(
    resp: requests.Response,
    *,
    max_events: int = 10_000,
) -> Iterator[_SSEEnvelope]:
    """
    Minimal SSE parser for frames like:

      event: <name>
      data: <payload>

    Frames are separated by a blank line. Comment lines (`: connected`, `: keepalive`)
    are ignored.
    """
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> _SSEEnvelope | None:
        nonlocal event_name, data_lines
        if event_name is None:
            return None
        env = {"event": event_name, "data": "\n".join(data_lines)}
        event_name = None
        data_lines = []
        return env

    emitted = 0
    for raw in resp.iter_lines(delimiter="\n", decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\r")
        if line == "":
            env = flush()
            if env is not None:
                yield env
                emitted += 1
                if emitted >= max_events:
                    raise PipeshubClientError(f"SSE exceeded max_events={max_events}")
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
            continue

    env = flush()
    if env is not None:
        yield env


def _format_http_error(resp: requests.Response) -> str:
    p = urlparse(resp.url or "")
    loc = (
        f"{p.scheme}://{p.netloc}{p.path or '/'}"
        if p.netloc
        else (resp.url or "")
    )
    msg = f"HTTP {resp.status_code} {loc}"
    try:
        error_data = resp.json()
        if error_data:
            msg += f" - {error_data}"
    except Exception:
        if resp.text:
            msg += f" - {resp.text[:200]}"
    return msg


def parse_kb_upload_response(resp: requests.Response) -> dict[str, Any]:
    """
    Parse a KB upload response into a normalized dict:

        {
          "records": [{"recordId": "...", "fileName": "...", ...}],
          "failed": [...],
          "summary": {"total": 1, "succeeded": 1, "failed": 0},
        }
    """
    if resp.status_code >= 400:
        msg = _format_http_error(resp)
        if resp.status_code == 401:
            raise PipeshubAuthError(msg)
        raise PipeshubClientError(msg)

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "text/event-stream" not in content_type and not resp.text.strip().startswith(":"):
        # Legacy JSON upload response (if ever reintroduced).
        try:
            legacy = resp.json()
            if isinstance(legacy, dict):
                return legacy
        except ValueError:
            pass
        raise PipeshubClientError(
            f"Expected text/event-stream KB upload response, got Content-Type={content_type!r}"
        )

    records: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None

    for envelope in iter_sse_envelopes(resp):
        event = envelope["event"]
        try:
            payload = json.loads(envelope["data"]) if envelope["data"] else {}
        except json.JSONDecodeError as exc:
            raise PipeshubClientError(
                f"Invalid JSON in SSE event {event!r}: {envelope['data']!r}"
            ) from exc

        if event == "file:succeeded":
            records.append(payload)
        elif event == "file:failed":
            failed.append(payload)
        elif event == "done":
            summary = payload.get("summary") if isinstance(payload, dict) else None
        elif event == "error":
            message = payload.get("message") if isinstance(payload, dict) else envelope["data"]
            raise PipeshubClientError(f"KB upload stream error: {message}")

    if not records:
        raise PipeshubClientError(
            f"KB upload produced no successful records (failed={failed!r}, summary={summary!r})"
        )

    return {
        "records": records,
        "failed": failed,
        "summary": summary,
    }
