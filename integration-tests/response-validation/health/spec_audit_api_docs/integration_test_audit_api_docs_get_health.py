"""Strict OpenAPI audit of GET /api/v1/docs/health."""

from __future__ import annotations

import pytest
from api_docs_audit_support import HEALTH_ROUTE, ApiDocsClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/docs/health"
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
    assert_strict_openapi_response(resp, ROUTE)


def test_health_ignores_query_parameters(pipeshub_client: PipeshubClient) -> None:
    resp = pipeshub_client.request(
        "GET", HEALTH_ROUTE, auth=False, params={"verbose": "true", "service": "x"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == HEALTH_BODY
    assert_strict_openapi_response(resp, ROUTE)
