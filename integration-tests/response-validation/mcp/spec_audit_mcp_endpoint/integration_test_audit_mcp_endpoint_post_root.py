"""Strict OpenAPI audit of POST /mcp.

A request answer is a short ``text/event-stream`` that ends once every request is answered.
The checker does not read such bodies, so each frame is checked here against
``McpJsonRpcResponse``.
"""

from __future__ import annotations

from typing import Any

import pytest
from helper.openapi_search_validator import assert_matches_component_schema
from mcp_endpoint_audit_support import (
    JSON_MEDIA_TYPE,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    JSONRPC_TRANSPORT_ERROR,
    MALFORMED_TOKEN,
    PROTOCOL_VERSION_HEADER,
    SSE_MEDIA_TYPE,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpEndpointClient,
    bearer,
    initialize_message,
    request_message,
    sse_messages,
)
from strict_openapi import assert_spec_forbids_request, assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit

ROUTE = "/mcp"
TOOLS_LIST = request_message("tools/list", 2)


def _answers(resp: Any) -> list[dict[str, Any]]:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers["Content-Type"].startswith(SSE_MEDIA_TYPE), resp.headers
    assert_strict_openapi_exchange(resp, ROUTE)
    messages = sse_messages(resp)
    for message in messages:
        assert_matches_component_schema(message, "McpJsonRpcResponse")
    return messages


def _transport_error(resp: Any, status: int, code: int) -> str:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["id"] is None, body
    assert body["error"]["code"] == code, body
    return str(body["error"]["message"])


def test_initialize_negotiates_the_protocol(mcp_endpoint_client: McpEndpointClient) -> None:
    [answer] = _answers(mcp_endpoint_client.post_message(initialize_message("spec-audit-init")))
    assert answer["id"] == "spec-audit-init"
    result = answer["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "pipeshub-mcp"
    assert "tools" in result["capabilities"]


def test_tools_list_names_the_pipeshub_tools(mcp_endpoint_client: McpEndpointClient) -> None:
    [answer] = _answers(mcp_endpoint_client.post_message(TOOLS_LIST))
    names = [tool["name"] for tool in answer["result"]["tools"]]
    assert "pipeshub_sources" in names, names


def test_tools_call_runs_a_tool_with_the_callers_token(mcp_endpoint_client: McpEndpointClient) -> None:
    call = request_message("tools/call", 3, {"name": "pipeshub_sources", "arguments": {}})
    [answer] = _answers(mcp_endpoint_client.post_message(call, timeout=120))
    assert answer["result"].get("isError") is not True, answer
    assert answer["result"]["content"][0]["type"] == "text"


def test_a_batch_is_answered_message_by_message(mcp_endpoint_client: McpEndpointClient) -> None:
    batch = [TOOLS_LIST, request_message("prompts/list", 4)]
    answers = _answers(mcp_endpoint_client.post_message(batch))
    assert sorted(a["id"] for a in answers) == [2, 4]


@pytest.mark.parametrize(
    ("message", "check"),
    [
        pytest.param(
            request_message("spec-audit/no-such-method", 5),
            lambda a: a["error"]["code"] == JSONRPC_METHOD_NOT_FOUND,
            id="unknown-method",
        ),
        pytest.param(
            request_message("initialize", 6, {}),
            lambda a: a["error"]["code"] == JSONRPC_INTERNAL_ERROR,
            id="initialize-without-its-params",
        ),
        pytest.param(
            request_message("tools/call", 7, {"name": "spec-audit-no-such-tool", "arguments": {}}),
            lambda a: a["result"]["isError"] is True,
            id="unknown-tool",
        ),
    ],
)
def test_jsonrpc_failures_are_answered_inside_a_200(
    mcp_endpoint_client: McpEndpointClient, message: dict[str, Any], check: Any
) -> None:
    [answer] = _answers(mcp_endpoint_client.post_message(message))
    assert answer["id"] == message["id"]
    assert check(answer), answer


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"jsonrpc": "2.0", "method": "notifications/initialized"}, id="notification"),
        pytest.param({"jsonrpc": "2.0", "id": 9, "result": {}}, id="client-response"),
        pytest.param({"jsonrpc": "2.0", "id": 9, "error": {"code": -1, "message": "spec-audit"}}, id="client-error"),
        pytest.param({"jsonrpc": "2.0", "error": {"code": -1, "message": "spec-audit"}}, id="client-error-without-id"),
        pytest.param(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": {"progressToken": 7, "x": 1}}},
            id="notification-with-meta",
        ),
        pytest.param([], id="empty-batch"),
    ],
)
def test_nothing_to_answer_is_accepted_without_a_body(mcp_endpoint_client: McpEndpointClient, body: Any) -> None:
    resp = mcp_endpoint_client.post_message(body)
    assert resp.status_code == 202, resp.text[:500]
    assert resp.content == b""
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"id": 2, "method": "tools/list"}, id="missing-jsonrpc"),
        pytest.param({**TOOLS_LIST, "jsonrpc": "1.0"}, id="jsonrpc-not-2.0"),
        pytest.param({**TOOLS_LIST, "id": None}, id="id-null"),
        pytest.param({**TOOLS_LIST, "id": 1.5}, id="id-not-an-integer"),
        pytest.param({**TOOLS_LIST, "params": [1]}, id="params-not-an-object"),
        pytest.param({**TOOLS_LIST, "spec_audit": 1}, id="unknown-field"),
        pytest.param({"jsonrpc": "2.0", "id": 2}, id="neither-method-nor-result"),
        pytest.param([{"jsonrpc": "2.0"}], id="invalid-message-in-a-batch"),
        pytest.param({**TOOLS_LIST, "result": {}}, id="request-with-result"),
        pytest.param(
            {"jsonrpc": "2.0", "id": 2, "result": {}, "error": {"code": -1, "message": "x"}}, id="result-with-error"
        ),
        pytest.param({"jsonrpc": "2.0", "method": "tools/list", "error": {"code": -1, "message": "x"}},
                     id="notification-with-error"),
        pytest.param({**TOOLS_LIST, "params": {"_meta": 5}}, id="request-meta-not-an-object"),
        pytest.param({**TOOLS_LIST, "params": {"_meta": {"progressToken": True}}}, id="progress-token-not-a-string"),
        pytest.param(
            {**TOOLS_LIST, "params": {"_meta": {"io.modelcontextprotocol/related-task": {}}}},
            id="related-task-without-task-id",
        ),
        pytest.param({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": 5}},
                     id="notification-meta-not-an-object"),
        pytest.param({"jsonrpc": "2.0", "id": 9, "result": {"_meta": 5}}, id="result-meta-not-an-object"),
        pytest.param({"jsonrpc": "2.0", "id": 9, "error": {"code": 1.5, "message": "x"}}, id="error-code-not-an-integer"),
    ],
)
def test_invalid_jsonrpc_message_is_a_parse_error(mcp_endpoint_client: McpEndpointClient, body: Any) -> None:
    resp = mcp_endpoint_client.post_message(body)
    message = _transport_error(resp, 400, JSONRPC_PARSE_ERROR)
    assert message == "Parse error: Invalid JSON-RPC message"
    assert_spec_forbids_request(resp, ROUTE)


def test_missing_body_is_a_parse_error(mcp_endpoint_client: McpEndpointClient) -> None:
    resp = mcp_endpoint_client.post("", headers={"Accept": "application/json, text/event-stream",
                                                 "Content-Type": JSON_MEDIA_TYPE})
    _transport_error(resp, 400, JSONRPC_PARSE_ERROR)
    assert_spec_forbids_request(resp, ROUTE)


def test_initialize_inside_a_larger_batch_is_an_invalid_request(mcp_endpoint_client: McpEndpointClient) -> None:
    # The schema cannot say "initialize must be alone"; the 400 description does.
    resp = mcp_endpoint_client.post_message([initialize_message(), TOOLS_LIST])
    message = _transport_error(resp, 400, JSONRPC_INVALID_REQUEST)
    assert "Only one initialization request is allowed" in message


def test_unsupported_protocol_version_is_refused_after_initialize(mcp_endpoint_client: McpEndpointClient) -> None:
    headers = {PROTOCOL_VERSION_HEADER: UNSUPPORTED_PROTOCOL_VERSION}
    resp = mcp_endpoint_client.post_message(TOOLS_LIST, headers=headers)
    message = _transport_error(resp, 400, JSONRPC_TRANSPORT_ERROR)
    assert message.startswith(f"Bad Request: Unsupported protocol version: {UNSUPPORTED_PROTOCOL_VERSION}")

    # initialize itself ignores the header.
    _answers(mcp_endpoint_client.post_message(initialize_message(), headers=headers))


@pytest.mark.parametrize("accept", [JSON_MEDIA_TYPE, SSE_MEDIA_TYPE, "*/*"])
def test_accept_without_both_media_types_is_not_acceptable(
    mcp_endpoint_client: McpEndpointClient, accept: str
) -> None:
    resp = mcp_endpoint_client.post_message(TOOLS_LIST, accept=accept)
    message = _transport_error(resp, 406, JSONRPC_TRANSPORT_ERROR)
    assert message == "Not Acceptable: Client must accept both application/json and text/event-stream"


def test_non_json_content_type_is_unsupported(mcp_endpoint_client: McpEndpointClient) -> None:
    resp = mcp_endpoint_client.post(
        "",
        data='{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}',
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "text/plain"},
    )
    message = _transport_error(resp, 415, JSONRPC_TRANSPORT_ERROR)
    assert message == "Unsupported Media Type: Content-Type must be application/json"


def test_json_that_is_not_an_object_or_array_is_an_internal_error(mcp_endpoint_client: McpEndpointClient) -> None:
    # API bug: the strict JSON body parser refuses a bare string with a 500, before auth.
    resp = mcp_endpoint_client.post_message("spec-audit", auth=False)
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        pytest.param({}, "No token provided", id="no-token"),
        pytest.param(bearer(MALFORMED_TOKEN), "Invalid token", id="malformed-token"),
    ],
)
def test_without_valid_token_is_unauthorized(
    mcp_endpoint_client: McpEndpointClient, headers: dict[str, str], message: str
) -> None:
    resp = mcp_endpoint_client.post_message(TOOLS_LIST, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == message
