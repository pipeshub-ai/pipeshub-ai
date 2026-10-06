"""Strict OpenAPI audit of GET /api/v1/artifacts.

authenticate -> requireScopes(kb:read | connector:read) -> zod query -> proxy to the
connectors service (list_artifacts_gallery) -> conversation title join.
"""

from __future__ import annotations

from typing import Any

import pytest
from artifacts_audit_support import (
    GALLERY_ARTIFACT_TYPES,
    GALLERY_SORT_FIELDS,
    SCOPE_REFUSAL,
    ArtifactsClient,
    SeededArtifact,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/artifacts"

Query = dict[str, Any] | list[tuple[str, str]]


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
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_every_parameter_finds_the_artifact(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    resp = artifacts_client.list(
        page=1,
        limit=100,
        search=seeded_artifact.name,
        artifactTypes=",".join(GALLERY_ARTIFACT_TYPES),
        conversationId=seeded_artifact.conversation_id,
        dateFrom=seeded_artifact.created_at - 60_000,
        dateTo=seeded_artifact.created_at + 60_000,
        sortBy="createdAtTimestamp",
        sortOrder="desc",
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert [item["artifactId"] for item in resp.json()["items"]] == [seeded_artifact.artifact_id]


@pytest.mark.parametrize(
    "window",
    [
        pytest.param({"dateFrom": 60_000}, id="created-before-dateFrom"),
        pytest.param({"dateTo": -60_000}, id="created-after-dateTo"),
    ],
)
def test_list_date_window_that_misses_the_artifact_is_empty(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact, window: dict[str, int]
) -> None:
    bounds = {name: seeded_artifact.created_at + offset for name, offset in window.items()}

    resp = artifacts_client.list(conversationId=seeded_artifact.conversation_id, **bounds)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["items"] == []


@pytest.mark.parametrize("sort_by", GALLERY_SORT_FIELDS)
def test_list_accepts_every_sort_field(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact, sort_by: str
) -> None:
    resp = artifacts_client.list(conversationId=seeded_artifact.conversation_id, sortBy=sort_by)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert len(resp.json()["items"]) == 1


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
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": ""}, id="page"),
        pytest.param({"limit": ""}, id="limit"),
        pytest.param({"search": ""}, id="search"),
        pytest.param({"artifactTypes": ""}, id="artifactTypes"),
        pytest.param({"conversationId": ""}, id="conversationId"),
        pytest.param({"dateFrom": ""}, id="dateFrom"),
        pytest.param({"dateTo": ""}, id="dateTo"),
        # Blank list items are dropped, so this is the same as "DOCUMENT,CODE".
        pytest.param({"artifactTypes": " DOCUMENT , ,CODE,"}, id="artifactTypes-with-blank-items"),
    ],
)
def test_list_reads_an_empty_value_as_not_sent(
    artifacts_client: ArtifactsClient, params: dict[str, str]
) -> None:
    resp = artifacts_client.list(**params)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["pagination"]["page"] == 1
    assert resp.json()["pagination"]["limit"] == 50


def test_list_ignores_unknown_query_parameters(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    with outside_request_contract("the validator drops query keys it does not know"):
        resp = artifacts_client.list(
            conversationId=seeded_artifact.conversation_id, visibility="HIDDEN", orgId="another-org"
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert [item["artifactId"] for item in resp.json()["items"]] == [seeded_artifact.artifact_id]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"page": "0"}, id="page-zero"),
        pytest.param({"page": "abc"}, id="page-not-a-number"),
        pytest.param({"limit": "0"}, id="limit-zero"),
        pytest.param({"limit": "101"}, id="limit-over-100"),
        pytest.param({"search": "s" * 201}, id="search-over-200"),
        # Python's gallery allows OTHER; the Node validator refuses it before the proxy.
        pytest.param({"artifactTypes": "DOCUMENT,OTHER"}, id="artifactTypes-outside-the-node-list"),
        pytest.param({"artifactTypes": "document"}, id="artifactTypes-lower-case"),
        pytest.param({"artifactTypes": ",".join(["DOCUMENT"] * 23)}, id="artifactTypes-over-200-characters"),
        pytest.param({"conversationId": "c" * 129}, id="conversationId-over-128"),
        pytest.param({"dateFrom": "-1"}, id="dateFrom-negative"),
        pytest.param({"dateFrom": "yesterday"}, id="dateFrom-not-a-number"),
        pytest.param({"dateTo": "-1"}, id="dateTo-negative"),
        pytest.param({"sortBy": "size"}, id="sortBy-unknown"),
        pytest.param({"sortBy": ""}, id="sortBy-empty"),
        pytest.param({"sortOrder": "up"}, id="sortOrder-unknown"),
        pytest.param({"sortOrder": ""}, id="sortOrder-empty"),
        pytest.param([("page", "1"), ("page", "2")], id="page-repeated"),
    ],
)
def test_list_rejects_a_query_outside_the_validator(
    artifacts_client: ArtifactsClient, params: Query
) -> None:
    resp = artifacts_client.get("", params=params)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    "params",
    [
        # parseInt("1abc") is 1 and Number("1.5") is a number, so zod passes them;
        # FastAPI's int query then refuses the same text.
        pytest.param({"page": "1abc"}, id="page-with-trailing-text"),
        pytest.param({"page": "1.5"}, id="page-decimal"),
        pytest.param({"limit": "5 items"}, id="limit-with-trailing-text"),
        pytest.param({"dateFrom": "1.5"}, id="dateFrom-decimal"),
        pytest.param({"dateFrom": "1e3"}, id="dateFrom-exponent"),
        pytest.param({"dateTo": "1.5"}, id="dateTo-decimal"),
    ],
)
def test_list_number_only_the_node_validator_accepts_is_unprocessable(
    artifacts_client: ArtifactsClient, params: dict[str, str]
) -> None:
    resp = artifacts_client.list(**params)

    assert resp.status_code == 422, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_UNPROCESSABLE_ENTITY"
    assert_strict_openapi_response(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_list_rejects_unauthenticated_calls(
    artifacts_client: ArtifactsClient, headers: dict[str, str]
) -> None:
    resp = artifacts_client.list(auth=False, headers=headers)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_list_with_a_token_lacking_both_read_scopes_is_forbidden(
    artifacts_client: ArtifactsClient, unscoped_headers: dict[str, str]
) -> None:
    resp = artifacts_client.list(auth=False, headers=unscoped_headers)

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == SCOPE_REFUSAL
    assert_strict_openapi_exchange(resp, ROUTE)
