"""Strict OpenAPI audit of GET /api/v1/docs* (the HTML documentation UI)."""

from __future__ import annotations

import pytest
import requests
from api_docs_audit_support import UI_ROUTE, UNKNOWN_SUB_PATH, ApiDocsClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

# The checker keeps the "*" of router.get('*') literally, so the mount path is the template.
ROUTE = UI_ROUTE
PAGE_TITLE = "<title>PipesHub API Documentation</title>"


def _assert_docs_page(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers.get("Content-Type", "").startswith("text/html"), (
        resp.headers.get("Content-Type")
    )
    assert PAGE_TITLE in resp.text, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize("auth", [True, False], ids=["with_token", "without_token"])
def test_docs_root_is_public_html(api_docs_client: ApiDocsClient, auth: bool) -> None:
    _assert_docs_page(api_docs_client.ui(auth=auth))


@pytest.mark.parametrize(
    "sub_path", ["/", UNKNOWN_SUB_PATH], ids=["trailing_slash", "unknown_sub_path"]
)
def test_docs_wildcard_serves_the_page_instead_of_404(
    api_docs_client: ApiDocsClient, sub_path: str
) -> None:
    _assert_docs_page(api_docs_client.ui(sub_path, auth=False))


def test_docs_root_ignores_query_parameters(pipeshub_client: PipeshubClient) -> None:
    resp = pipeshub_client.request(
        "GET", UI_ROUTE, auth=False, params={"module": "no-such-module", "page": "-1"}
    )
    _assert_docs_page(resp)
