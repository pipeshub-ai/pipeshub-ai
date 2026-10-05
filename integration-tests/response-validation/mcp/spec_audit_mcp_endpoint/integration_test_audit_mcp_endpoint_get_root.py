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
    # The transport checks Accept before anything else.
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


@pytest.mark.xfail(
    strict=True,
    reason="API bug: stateless GET /mcp opens an SSE stream that can never carry an event "
    "instead of the 405 its route documents",
)
def test_get_accepting_sse_is_method_not_allowed_in_stateless_mode(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    # mcp.routes.ts documents 405 for the stateless transport; the SDK answers 200 and holds an idle stream.
    resp = mcp_endpoint_client.open_stream()
    assert resp.status_code == 405, f"{resp.status_code} {resp.headers.get('Content-Type')} {resp.text[:500]}"
    assert_strict_openapi_response(resp, ROUTE)
