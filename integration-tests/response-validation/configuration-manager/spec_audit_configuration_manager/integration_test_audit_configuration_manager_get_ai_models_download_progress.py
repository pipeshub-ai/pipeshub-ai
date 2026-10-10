"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/download-progress.

Chain: authenticate -> requireScopes(config:read) -> userAdminCheck -> streamEmbeddingDownloadProgress.
No validator: the handler checks ``model`` itself. The 200 is a text/event-stream, which the
strict gate does not read, so the frames are checked here.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
import requests
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    MALFORMED_EMBEDDING_MODEL,
    UNKNOWN_EMBEDDING_MODEL,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_request,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/download-progress"
PATH = "/ai-models/download-progress"
INVALID_MODEL_BODY = {"status": "error", "message": "A valid model query parameter is required"}

# (connect, read): each frame follows one poll of the embedding server (axios timeout 5s).
STREAM_TIMEOUT = (10, 20)


def _sse_frames(resp: requests.Response, count: int) -> list[tuple[float, bytes]]:
    """Read ``count`` SSE frames, each with the time it arrived, and stop.

    For a model the embedding server never tracked the status stays "not_found",
    which is not terminal, so the server keeps the stream open indefinitely.
    """
    frames: list[tuple[float, bytes]] = []
    lines: list[bytes] = []
    for line in resp.iter_lines():
        if line:
            lines.append(line)
            continue
        if lines:
            frames.append((time.monotonic(), b"\n".join(lines) + b"\n\n"))
            lines = []
            if len(frames) == count:
                break
    resp.close()
    return frames


def _parse_frame(frame: bytes) -> tuple[str, dict[str, Any]]:
    fields = dict(line.split(": ", 1) for line in frame.decode().splitlines() if ": " in line)
    return fields["event"], json.loads(fields["data"])


def test_admin_stream_repeats_the_progress_frame_for_an_untracked_model(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, params={"model": UNKNOWN_EMBEDDING_MODEL}, stream=True, timeout=STREAM_TIMEOUT)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        assert resp.headers["Cache-Control"] == "no-cache"
        frames = _sse_frames(resp, 2)
    finally:
        resp.close()
    assert_strict_openapi_request(resp, ROUTE)

    assert len(frames) == 2, frames
    for _, frame in frames:
        event, data = _parse_frame(frame)
        assert event == "progress"
        assert data["model"] == UNKNOWN_EMBEDDING_MODEL
        # "failed" is what Node writes itself when the embedding server is unreachable; it ends the stream.
        assert data["status"] == "not_found", data
        assert isinstance(data["timestamp"], int)
        assert {"progress", "downloaded_bytes", "total_bytes", "error"} <= data.keys()
    # The handler waits 3 seconds between polls.
    assert frames[1][0] - frames[0][0] >= 2.5, frames


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(None, id="missing-model"),
        pytest.param({"model": MALFORMED_EMBEDDING_MODEL}, id="malformed-model"),
        pytest.param({"model": "a/b/c"}, id="two-slashes"),
        pytest.param({"model": ""}, id="empty-model"),
    ],
)
def test_invalid_model_query_is_refused_by_the_handler(
    config_client: ConfigClient, params: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, params=params)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == INVALID_MODEL_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.get(PATH, auth=False, headers=headers, params={"model": UNKNOWN_EMBEDDING_MODEL})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden_before_stream_opens(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH, params={"model": UNKNOWN_EMBEDDING_MODEL})
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
