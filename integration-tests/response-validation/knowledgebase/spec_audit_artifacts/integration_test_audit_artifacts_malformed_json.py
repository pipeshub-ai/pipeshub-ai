"""Every /api/v1/artifacts route answers 500 to a malformed JSON body.

The JSON body parser runs before the router, so these GET routes fail on a body they
never read, before the token is checked.
"""

from __future__ import annotations

import pytest
from artifacts_audit_support import (
    ARTIFACTS_BASE,
    JSON_HEADERS,
    MALFORMED_JSON_BODY,
    MISSING_ARTIFACT_ID,
    ArtifactsClient,
)
from strict_openapi import assert_strict_openapi_exchange

pytestmark = pytest.mark.spec_audit


@pytest.mark.parametrize(
    ("sub_path", "route"),
    [
        ("", ARTIFACTS_BASE),
        (f"/{MISSING_ARTIFACT_ID}", f"{ARTIFACTS_BASE}/:artifactId"),
        (f"/{MISSING_ARTIFACT_ID}/versions", f"{ARTIFACTS_BASE}/:artifactId/versions"),
    ],
    ids=["list", "detail", "versions"],
)
def test_malformed_json_body_is_an_internal_error_before_the_token_check(
    artifacts_client: ArtifactsClient, sub_path: str, route: str
) -> None:
    resp = artifacts_client.get(
        sub_path, auth=False, data=MALFORMED_JSON_BODY, headers=JSON_HEADERS
    )
    assert resp.status_code == 500, resp.text[:500]
    assert resp.json()["error"]["code"] == "INTERNAL_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, route)
