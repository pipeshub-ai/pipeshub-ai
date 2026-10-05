"""Strict OpenAPI audit of GET /api/v1/artifacts/:artifactId/versions."""

from __future__ import annotations

import pytest
from artifacts_audit_support import (
    MALFORMED_ARTIFACT_ID,
    MISSING_ARTIFACT_ID,
    ArtifactsClient,
    SeededArtifact,
    request_as,
)
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/artifacts/:artifactId/versions"


def test_versions_lists_every_registry_version_in_order(
    artifacts_client: ArtifactsClient, seeded_artifact: SeededArtifact
) -> None:
    resp = artifacts_client.versions(seeded_artifact.artifact_id)

    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert set(body) == {"versions"}
    versions = body["versions"]
    assert [v["version"] for v in versions] == list(seeded_artifact.version_numbers)
    latest = versions[-1]
    assert latest["sizeBytes"] == seeded_artifact.size_in_bytes
    assert latest["contentHash"] == seeded_artifact.content_hash
    assert_strict_openapi_response(resp, ROUTE)


def test_versions_without_token_is_unauthorized(artifacts_client: ArtifactsClient) -> None:
    resp = artifacts_client.versions(MISSING_ARTIFACT_ID, auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    ("artifact_id", "expected_status"),
    [
        pytest.param(MALFORMED_ARTIFACT_ID, 400, id="malformed-id"),
        pytest.param(MISSING_ARTIFACT_ID, 404, id="missing-id"),
    ],
)
def test_versions_rejects_unusable_id(
    artifacts_client: ArtifactsClient, artifact_id: str, expected_status: int
) -> None:
    resp = artifacts_client.versions(artifact_id)

    assert resp.status_code == expected_status, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_versions_of_another_users_artifact_is_not_found(
    second_user: SecondUser, seeded_artifact: SeededArtifact
) -> None:
    # No admin or scope gate applies to a session JWT; the graph permission lookup hides the row.
    resp = request_as(second_user, f"/{seeded_artifact.artifact_id}/versions")

    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
