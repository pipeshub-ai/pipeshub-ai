"""Strict OpenAPI audit of POST /api/v1/configurationManager/storageConfig.

Negative paths only: a successful call switches the org-wide storage backend.
"""

from __future__ import annotations

from typing import Any

import pytest
from configuration_manager_audit_support import request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/storageConfig"

LOCAL_STORAGE_BODY: dict[str, Any] = {"storageType": "local"}


def test_create_storage_config_without_token_is_unauthorized(config_client: ConfigClient) -> None:
    resp = config_client.post("/storageConfig", auth=False, json=LOCAL_STORAGE_BODY)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_create_storage_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/storageConfig", json=LOCAL_STORAGE_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


@pytest.mark.parametrize(
    "body",
    [
        # "gcp" is a storageTypes constant but not a member of the discriminated union.
        pytest.param({"storageType": "gcp"}, id="unsupported-storage-type"),
        pytest.param({"storageType": "s3", "s3Region": "us-east-1"}, id="s3-missing-bucket"),
        # Refused by the zod refinement, before any S3 health check runs.
        pytest.param(
            {
                "storageType": "s3",
                "s3Region": "us-east-1",
                "s3BucketName": "spec-audit-bucket",
                "s3AccessKeyId": "AKIASPECAUDIT",
            },
            id="s3-half-credential-pair",
        ),
    ],
)
def test_create_storage_config_rejects_invalid_body(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post("/storageConfig", json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
