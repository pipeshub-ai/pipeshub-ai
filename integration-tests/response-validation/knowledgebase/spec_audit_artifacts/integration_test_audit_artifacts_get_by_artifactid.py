"""Strict OpenAPI audit of GET /api/v1/artifacts/:artifactId."""

from __future__ import annotations

import pytest
from artifacts_audit_support import (
    MALFORMED_ARTIFACT_ID,
    MISSING_ARTIFACT_ID,
    OVERLONG_ARTIFACT_ID,
    SCOPE_REFUSAL,
    ArtifactsClient,
    SeededArtifact,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/artifacts/:artifactId"


def test_get_returns_detail_with_versions(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    resp = artifacts_client.get_one(seeded_artifact.artifact_id)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["artifactId"] == seeded_artifact.artifact_id
    assert body["name"] == seeded_artifact.name
    assert body["artifactType"] == seeded_artifact.artifact_type
    assert body["mimeType"] == seeded_artifact.mime_type
    assert body["version"] == seeded_artifact.version
    assert body["conversationId"] == seeded_artifact.conversation_id
    # The seed's conversation id has no chat behind it, so Node adds no title.
    assert "conversationTitle" not in body
    assert body["description"] == "Seeded by the artifacts spec audit"
    assert body["sourceTool"] == "spec_audit.seed"
    assert (
        tuple(v["version"] for v in body["versions"]) == seeded_artifact.version_numbers
    )
    assert all(
        {"version", "sizeBytes", "contentHash", "createdAt"} <= v.keys()
        for v in body["versions"]
    )


def test_get_another_users_artifact_is_not_found(
    second_user: SecondUser, seeded_artifact: SeededArtifact
) -> None:
    # No permission edge: Python hides existence behind a 404 rather than a 403.
    resp = request_as(second_user, f"/{seeded_artifact.artifact_id}")
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "Artifact not found" in resp.text


@pytest.mark.parametrize(
    "artifact_id",
    [MALFORMED_ARTIFACT_ID, OVERLONG_ARTIFACT_ID],
    ids=["not-path-safe", "longer-than-128"],
)
def test_get_invalid_artifact_id_is_rejected(
    artifacts_client: ArtifactsClient, artifact_id: str
) -> None:
    resp = artifacts_client.get_one(artifact_id)
    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_unknown_artifact_is_not_found(artifacts_client: ArtifactsClient) -> None:
    resp = artifacts_client.get_one(MISSING_ARTIFACT_ID)
    assert resp.status_code == 404, resp.text[:500]
    assert resp.json()["error"]["message"] == "Artifact not found"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_reads_neither_query_nor_body(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    with outside_request_contract("the validator checks only the artifactId path parameter"):
        resp = artifacts_client.get_one(
            seeded_artifact.artifact_id, params={"version": "1", "include": "content"}, json={"version": 1}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["version"] == seeded_artifact.version


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-token"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="invalid-token"),
    ],
)
def test_get_rejects_unauthenticated_calls(
    artifacts_client: ArtifactsClient, headers: dict[str, str]
) -> None:
    resp = artifacts_client.get_one(MISSING_ARTIFACT_ID, auth=False, headers=headers)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_get_with_a_token_lacking_both_read_scopes_is_forbidden(
    artifacts_client: ArtifactsClient, unscoped_headers: dict[str, str]
) -> None:
    resp = artifacts_client.get_one(MISSING_ARTIFACT_ID, auth=False, headers=unscoped_headers)
    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["message"] == SCOPE_REFUSAL
    assert_strict_openapi_exchange(resp, ROUTE)
