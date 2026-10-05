"""Strict OpenAPI audit of GET /mcp."""

from __future__ import annotations

import pytest
from mcp_endpoint_audit_support import (
    JSON_MEDIA_TYPE,
    MALFORMED_TOKEN,
    PROTOCOL_VERSION_HEADER,
    SSE_MEDIA_TYPE,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpEndpointClient,
    bearer,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/mcp"
JSONRPC_TRANSPORT_ERROR = -32000


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        pytest.param({}, "No token provided", id="no-token"),
        pytest.param(bearer(MALFORMED_TOKEN), "Invalid token", id="malformed-token"),
    ],
)
def test_get_without_valid_token_is_unauthorized(
    mcp_endpoint_client: McpEndpointClient,
    headers: dict[str, str],
    message: str,
) -> None:
    resp = mcp_endpoint_client.get_root(auth=False, accept=SSE_MEDIA_TYPE, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["message"] == message


def test_get_without_sse_accept_is_not_acceptable(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    # The route comment and the spec promise 405; the stateless transport answers 406 instead.
    resp = mcp_endpoint_client.get_root(accept=JSON_MEDIA_TYPE)
    assert resp.status_code == 406, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {
        "jsonrpc": "2.0",
        "error": {
            "code": JSONRPC_TRANSPORT_ERROR,
            "message": "Not Acceptable: Client must accept text/event-stream",
        },
        "id": None,
    }


def test_get_with_unsupported_protocol_version_is_bad_request(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    resp = mcp_endpoint_client.open_stream(
        headers={PROTOCOL_VERSION_HEADER: UNSUPPORTED_PROTOCOL_VERSION}
    )
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["id"] is None
    assert body["error"]["code"] == JSONRPC_TRANSPORT_ERROR
    assert body["error"]["message"].startswith(
        f"Bad Request: Unsupported protocol version: {UNSUPPORTED_PROTOCOL_VERSION}"
    )


def test_get_accepting_sse_opens_idle_stream(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    resp = mcp_endpoint_client.open_stream()
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].split(";")[0].strip() == SSE_MEDIA_TYPE
    # Stateless transport: no session is ever issued, and nothing is pushed on the standalone stream.
    assert "mcp-session-id" not in resp.headers
    assert resp.content == b""
    assert_strict_openapi_response(resp, ROUTE)
