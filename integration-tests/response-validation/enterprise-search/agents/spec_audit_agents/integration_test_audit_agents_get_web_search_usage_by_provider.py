"""Strict OpenAPI audit of GET /api/v1/agents/web-search-usage/:provider."""

from __future__ import annotations

import pytest
from agents_audit_support import (
    UNKNOWN_WEB_SEARCH_PROVIDER,
    UNSAFE_PATH_SEGMENT,
    WEB_SEARCH_PROVIDERS,
    AgentsAuditClient,
)
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/agents/web-search-usage/:provider"
PROVIDER = WEB_SEARCH_PROVIDERS[0]


@pytest.mark.parametrize(
    "provider",
    [
        # Node and Python both lower-case the provider before matching it.
        pytest.param(PROVIDER.upper(), id="supported-provider-upper-case"),
        pytest.param(UNKNOWN_WEB_SEARCH_PROVIDER, id="unknown-provider"),
    ],
)
def test_usage_lists_the_agents_using_a_provider(
    agents_audit_client: AgentsAuditClient, provider: str
) -> None:
    resp = agents_audit_client.web_search_usage(provider)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True, body
    assert isinstance(body["agents"], list), body
    if provider == UNKNOWN_WEB_SEARCH_PROVIDER:
        assert body["agents"] == [], body


def test_usage_rejects_any_query_param(agents_audit_client: AgentsAuditClient) -> None:
    resp = agents_audit_client.web_search_usage(PROVIDER, params={"limit": "1"})
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_usage_rejects_a_provider_unsafe_as_a_path_segment(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.web_search_usage(UNSAFE_PATH_SEGMENT)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_usage_rejects_a_call_without_a_token(
    agents_audit_client: AgentsAuditClient,
) -> None:
    resp = agents_audit_client.web_search_usage(PROVIDER, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
