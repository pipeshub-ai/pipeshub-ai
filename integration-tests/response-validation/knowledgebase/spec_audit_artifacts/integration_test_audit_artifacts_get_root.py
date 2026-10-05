"""Strict OpenAPI audit of GET /api/v1/artifacts.

authenticate -> requireScopes(kb:read | connector:read) -> zod query -> proxy to the
connectors service (list_artifacts_gallery) -> conversation title join.
"""

from __future__ import annotations

import pytest
from artifacts_audit_support import ArtifactsClient, SeededArtifact, request_as
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/artifacts"


def test_list_returns_the_seeded_artifact_for_its_owner(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    resp = artifacts_client.list(
        conversationId=seeded_artifact.conversation_id,
        artifactTypes=seeded_artifact.artifact_type,
        sortBy="name",
        sortOrder="asc",
        limit=5,
    )

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"items", "pagination"}, body.keys()
    assert body["pagination"] == {"page": 1, "limit": 5, "totalCount": 1, "totalPages": 1}
    assert len(body["items"]) == 1, body["items"]
    item = body["items"][0]
    assert item["artifactId"] == seeded_artifact.artifact_id
    assert item["name"] == seeded_artifact.name
    assert item["artifactType"] == seeded_artifact.artifact_type
    assert item["version"] == seeded_artifact.version
    assert item["conversationId"] == seeded_artifact.conversation_id
    # No chat stands behind the seeded conversation id, so Node has no title to join.
    assert "conversationTitle" not in item
    assert_strict_openapi_response(resp, ROUTE)


def test_list_hides_another_users_artifact_from_a_member(
    seeded_artifact: SeededArtifact, second_user: SecondUser
) -> None:
    resp = request_as(
        second_user, params={"conversationId": seeded_artifact.conversation_id}
    )

    # A session JWT skips the scope check: the member is not refused, just sees nothing.
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert body["items"] == [], body["items"]
    assert body["pagination"]["totalCount"] == 0
    assert_strict_openapi_response(resp, ROUTE)


def test_list_without_token_is_unauthorized(artifacts_client: ArtifactsClient) -> None:
    resp = artifacts_client.list(auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_artifact_type_outside_the_node_list_is_rejected(
    artifacts_client: ArtifactsClient,
) -> None:
    # Python's gallery allows OTHER; the Node validator refuses it before the proxy.
    resp = artifacts_client.list(artifactTypes="DOCUMENT,OTHER")

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_list_page_that_only_parseint_accepts_is_unprocessable(
    artifacts_client: ArtifactsClient,
) -> None:
    # parseInt("1abc") is 1, so zod passes it; FastAPI's int query then refuses it.
    resp = artifacts_client.list(page="1abc")

    assert resp.status_code == 422, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
