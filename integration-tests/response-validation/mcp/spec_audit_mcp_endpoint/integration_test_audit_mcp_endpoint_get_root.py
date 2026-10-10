"""Strict OpenAPI audit of GET /mcp."""

from __future__ import annotations

import pytest
from mcp_endpoint_audit_support import (
    JSON_MEDIA_TYPE,
    JSONRPC_TRANSPORT_ERROR,
    MALFORMED_TOKEN,
    PROTOCOL_VERSION_HEADER,
    SSE_MEDIA_TYPE,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpEndpointClient,
    bearer,
)
from openapi_schema_validator import load_openapi_document
from strict_openapi import assert_strict_openapi_exchange, find_operation

pytestmark = pytest.mark.spec_audit

ROUTE = "/mcp"


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
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == message


def test_get_without_sse_accept_is_not_acceptable(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    # The transport checks Accept before anything else.
    resp = mcp_endpoint_client.get_root(accept=JSON_MEDIA_TYPE)
    assert resp.status_code == 406, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
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
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["id"] is None
    assert body["error"]["code"] == JSONRPC_TRANSPORT_ERROR
    assert body["error"]["message"].startswith(
        f"Bad Request: Unsupported protocol version: {UNSUPPORTED_PROTOCOL_VERSION}"
    )


def test_get_accepting_sse_opens_a_stream_that_never_carries_an_event(
    mcp_endpoint_client: McpEndpointClient,
) -> None:
    # API bug: mcp.routes.ts says stateless mode answers 405; the SDK opens an idle stream instead.
    resp = mcp_endpoint_client.open_stream()
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith(SSE_MEDIA_TYPE), resp.headers
    assert resp.content == b"", resp.content[:200]
    # The checker refuses an empty body where the spec documents one, so the spec is read here.
    found = find_operation(load_openapi_document(), "get", ROUTE)
    assert found is not None
    documented = found[1]["responses"]["200"]["content"]
    assert list(documented) == [SSE_MEDIA_TYPE], documented
    assert "405" not in found[1]["responses"]
