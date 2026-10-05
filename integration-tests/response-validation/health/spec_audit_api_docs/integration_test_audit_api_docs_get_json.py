"""Strict OpenAPI audit of GET /api/v1/docs/json."""

from __future__ import annotations

from typing import Any

import pytest
from api_docs_audit_support import JSON_ROUTE, UNIFIED_DOCS_KEYS, ApiDocsClient
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/docs/json"

MODULE_KEYS = {"id", "name", "description", "version", "basePath", "tags", "source", "order"}
ENDPOINT_KEYS = {
    "path",
    "method",
    "summary",
    "description",
    "operationId",
    "tags",
    "parameters",
    "responses",
    "moduleId",
}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _assert_unified_docs(body: Any) -> None:
    assert isinstance(body, dict), type(body)
    assert set(body) == set(UNIFIED_DOCS_KEYS), sorted(body)

    info = body["info"]
    assert {"title", "version", "description"} <= set(info), sorted(info)
    assert set(info) <= {"title", "version", "description", "contact"}, sorted(info)

    assert isinstance(body["categories"], list)
    assert isinstance(body["modules"], list)
    assert isinstance(body["endpoints"], list)
    assert isinstance(body["schemas"], dict)


@pytest.mark.parametrize("auth", [True, False], ids=["with_token", "without_token"])
def test_json_is_public_and_returns_unified_docs(
    api_docs_client: ApiDocsClient, auth: bool
) -> None:
    resp = api_docs_client.unified_json(auth=auth)
    assert resp.status_code == 200, resp.text[:500]
    assert resp.headers.get("Content-Type", "").startswith("application/json"), (
        resp.headers.get("Content-Type")
    )
    _assert_unified_docs(resp.json())
    assert_strict_openapi_response(resp, ROUTE)


def test_json_modules_categories_and_endpoints_are_consistent(
    api_docs_client: ApiDocsClient,
) -> None:
    resp = api_docs_client.unified_json(auth=False)
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    _assert_unified_docs(body)

    modules = body["modules"]
    assert modules, "the service registers its modules in the constructor"
    for module in modules:
        assert set(module) >= MODULE_KEYS, sorted(module)
        assert module["source"] in {"nodejs", "python"}, module
    orders = [module["order"] for module in modules]
    assert orders == sorted(orders), orders

    module_ids = {module["id"] for module in modules}
    for category in body["categories"]:
        assert set(category) == {"id", "name", "description", "modules"}, sorted(category)
        assert {m["id"] for m in category["modules"]} <= module_ids, category["id"]

    for endpoint in body["endpoints"]:
        assert set(endpoint) >= ENDPOINT_KEYS, endpoint.get("path")
        assert endpoint["method"] in HTTP_METHODS, endpoint
    assert_strict_openapi_response(resp, ROUTE)


def test_json_ignores_query_parameters(pipeshub_client: PipeshubClient) -> None:
    resp = pipeshub_client.request(
        "GET", JSON_ROUTE, auth=False, params={"module": "no-such-module", "format": "yaml"}
    )
    assert resp.status_code == 200, resp.text[:500]
    _assert_unified_docs(resp.json())
    assert_strict_openapi_response(resp, ROUTE)
