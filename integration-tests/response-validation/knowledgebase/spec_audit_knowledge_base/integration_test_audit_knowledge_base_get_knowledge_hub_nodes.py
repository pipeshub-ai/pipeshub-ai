"""Strict OpenAPI audit of GET /api/v1/knowledgeBase/knowledge-hub/nodes.

authenticate -> requireScopes(KB_READ) -> getKnowledgeHubNodes, which forwards the
query to the connector service (knowledge_hub_router.get_knowledge_hub_root_nodes).
Node has no validator here: every refusal below the token check is Python's.
"""

from __future__ import annotations

import pytest
from helper.clients.kb_client import KBClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/knowledgeBase/knowledge-hub/nodes"
PATH = "/knowledge-hub/nodes"
ALL_INCLUDES = "breadcrumbs,counts,availableFilters,permissions"


def test_root_listing_without_filters(kb_client: KBClient, audit_kb_id: str) -> None:
    resp = kb_client.get(PATH)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["items"], list)
    assert_strict_openapi_response(resp, ROUTE)


def test_filtered_listing_with_every_include(kb_client: KBClient, audit_kb_id: str) -> None:
    # A node type filter switches the handler to the flattened search across the tree.
    resp = kb_client.get(
        PATH,
        params={
            "nodeTypes": "app,recordGroup",
            "include": ALL_INCLUDES,
            "sortBy": "name",
            "sortOrder": "asc",
            "limit": 200,
        },
    )

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["items"], list)
    assert_strict_openapi_response(resp, ROUTE)


def test_without_token_is_unauthorized(kb_client: KBClient) -> None:
    resp = kb_client.get(PATH, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_one_character_search_is_bad_request(kb_client: KBClient) -> None:
    resp = kb_client.get(PATH, params={"q": "a"})

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_page_zero_is_unprocessable(kb_client: KBClient) -> None:
    # FastAPI rejects page < 1 with 422 and the connector service has no handler
    # that rewrites it; handleBackendError keeps 422 as UnprocessableEntityError.
    resp = kb_client.get(PATH, params={"page": 0})

    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
