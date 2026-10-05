"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/download-progress."""

from __future__ import annotations

import json
from typing import Any

import pytest
import requests
from configuration_manager_audit_support import (
    MALFORMED_EMBEDDING_MODEL,
    UNKNOWN_EMBEDDING_MODEL,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/download-progress"
PATH = "/ai-models/download-progress"

# (connect, read): the first frame follows one poll of the embedding server (axios timeout 5s).
STREAM_TIMEOUT = (10, 20)


def _first_sse_frame(resp: requests.Response) -> bytes:
    """Read one SSE frame and stop.

    For a model the embedding server never tracked the status stays "not_found",
    which is not terminal, so the server keeps the stream open indefinitely.
    """
    lines: list[bytes] = []
    for line in resp.iter_lines():
        if not line:
            if lines:
                break
            continue
        lines.append(line)
    frame = b"\n".join(lines) + b"\n\n"
    resp.close()
    # The strict checker reads resp.content, which raises once a stream was partly consumed.
    resp._content = frame
    return frame


def _parse_frame(frame: bytes) -> tuple[str, dict[str, Any]]:
    fields = dict(
        line.split(": ", 1) for line in frame.decode().splitlines() if ": " in line
    )
    return fields["event"], json.loads(fields["data"])


def test_admin_stream_emits_progress_frame_for_untracked_model(
    config_client: ConfigClient,
) -> None:
    resp = config_client.get(
        PATH,
        params={"model": UNKNOWN_EMBEDDING_MODEL},
        stream=True,
        timeout=STREAM_TIMEOUT,
    )
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        frame = _first_sse_frame(resp)
    finally:
        resp.close()
    assert_strict_openapi_response(resp, ROUTE)

    event, data = _parse_frame(frame)
    assert event == "progress"
    assert data["model"] == UNKNOWN_EMBEDDING_MODEL
    # "failed" is what Node writes itself when the embedding server is unreachable.
    assert data["status"] in ("not_found", "failed"), data
    assert isinstance(data["timestamp"], int)
    assert {"progress", "downloaded_bytes", "total_bytes", "error"} <= data.keys()


@pytest.mark.parametrize(
    "params",
    [None, {"model": MALFORMED_EMBEDDING_MODEL}],
    ids=["missing-model", "malformed-model"],
)
def test_invalid_model_query_is_bad_request(
    config_client: ConfigClient, params: dict[str, str] | None
) -> None:
    resp = config_client.get(PATH, params=params)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "status": "error",
        "message": "A valid model query parameter is required",
    }


def test_no_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.get(
        PATH, auth=False, params={"model": UNKNOWN_EMBEDDING_MODEL}
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden_before_stream_opens(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "GET", PATH, params={"model": UNKNOWN_EMBEDDING_MODEL}
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
