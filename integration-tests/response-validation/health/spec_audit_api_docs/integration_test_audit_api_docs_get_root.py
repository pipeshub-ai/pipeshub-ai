"""Strict OpenAPI audit of GET /api/v1/docs* (the HTML documentation UI)."""

from __future__ import annotations

import pytest
import requests
from api_docs_audit_support import (
    DEEPER_SUB_PATH,
    ONE_SEGMENT_SUB_PATH,
    UI_ROUTE,
    UI_SUB_PATH_ROUTE,
    ApiDocsClient,
)
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

PAGE_TITLE = "<title>PipesHub API Documentation</title>"


def _assert_docs_page(resp: requests.Response) -> None:
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers.get("Content-Type", "").startswith("text/html"), (
        resp.headers.get("Content-Type")
    )
    assert PAGE_TITLE in resp.text, resp.text[:500]


@pytest.mark.parametrize("auth", [True, False], ids=["with_token", "without_token"])
def test_docs_root_is_public_html(api_docs_client: ApiDocsClient, auth: bool) -> None:
    resp = api_docs_client.ui(auth=auth)
    _assert_docs_page(resp)
    assert_strict_openapi_exchange(resp, UI_ROUTE)


def test_docs_root_with_trailing_slash_is_the_same_page(
    api_docs_client: ApiDocsClient,
) -> None:
    resp = api_docs_client.ui("/", auth=False)
    _assert_docs_page(resp)
    assert resp.text == api_docs_client.ui(auth=False).text
    assert_strict_openapi_exchange(resp, UI_ROUTE)


@pytest.mark.parametrize("auth", [True, False], ids=["with_token", "without_token"])
def test_docs_unknown_sub_path_serves_the_page_instead_of_404(
    api_docs_client: ApiDocsClient, auth: bool
) -> None:
    resp = api_docs_client.ui(ONE_SEGMENT_SUB_PATH, auth=auth)
    _assert_docs_page(resp)
    assert resp.text == api_docs_client.ui(auth=False).text
    assert_strict_openapi_exchange(resp, UI_SUB_PATH_ROUTE)


@pytest.mark.parametrize("sub_path", [DEEPER_SUB_PATH, "/health/extra", "/json/extra"])
def test_docs_deeper_sub_path_serves_the_page_instead_of_404(
    api_docs_client: ApiDocsClient, sub_path: str
) -> None:
    # No OpenAPI path can match a URL with more segments than its template, so the gate
    # would report this call as an undocumented route. stream=True keeps it out of the
    # gate; the response is checked here against the catch-all operation instead.
    resp = api_docs_client.ui(sub_path, auth=False, stream=True)
    _assert_docs_page(resp)
    assert resp.text == api_docs_client.ui(auth=False).text
    assert_strict_openapi_response(resp, UI_SUB_PATH_ROUTE)


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [("", UI_ROUTE), (ONE_SEGMENT_SUB_PATH, UI_SUB_PATH_ROUTE)],
    ids=["root", "sub_path"],
)
def test_docs_page_ignores_query_parameters(
    api_docs_client: ApiDocsClient, sub_path: str, route: str
) -> None:
    with outside_request_contract("the handler never reads the query string"):
        resp = api_docs_client.ui(
            sub_path, auth=False, params={"module": "no-such-module", "page": "-1"}
        )
    _assert_docs_page(resp)
    assert_strict_openapi_response(resp, route)
