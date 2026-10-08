"""Strict OpenAPI audit of GET /api/v1/docs/health."""

from __future__ import annotations

import pytest
from api_docs_audit_support import HEALTH_ROUTE, ApiDocsClient
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = HEALTH_ROUTE
HEALTH_BODY = {"status": "ok", "service": "api-docs"}


@pytest.mark.parametrize("auth", [True, False], ids=["with_token", "without_token"])
def test_health_is_public_and_reports_ok(
    api_docs_client: ApiDocsClient, auth: bool
) -> None:
    resp = api_docs_client.health(auth=auth)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        resp.headers.get("Content-Type")
    )
    assert resp.json() == HEALTH_BODY
    assert_strict_openapi_exchange(resp, ROUTE)


def test_health_ignores_query_parameters(api_docs_client: ApiDocsClient) -> None:
    with outside_request_contract("the handler never reads the query string"):
        resp = api_docs_client.health(
            auth=False, params={"verbose": "true", "service": "x"}
        )
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == HEALTH_BODY
    assert_strict_openapi_response(resp, ROUTE)
