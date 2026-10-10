"""Strict OpenAPI audit of POST /api/v1/configurationManager/storageConfig.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> createStorageConfig.
The only success run here is ``local`` with its defaults, which is what this stack already
uses. A successful ``s3`` or ``azureBlob`` call moves every upload of the shared
deployment to another backend, so those two are exercised up to their refusals only.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    KV_STORAGE,
    assert_validation_error,
    read_stored_value,
    request_as,
    write_stored_value,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/storageConfig"
PATH = "/storageConfig"
SAVED_BODY = {"message": "Storage configuration saved successfully"}

LOCAL_BODY: dict[str, Any] = {"storageType": "local"}
S3_BODY: dict[str, Any] = {"storageType": "s3", "s3Region": "us-east-1", "s3BucketName": "spec-audit-bucket"}
AZURE_BODY: dict[str, Any] = {
    "storageType": "azureBlob",
    "accountName": "specauditaccount",
    "accountKey": "c3BlYy1hdWRpdA==",
    "containerName": "spec-audit",
}


@pytest.fixture
def local_storage_kept() -> Iterator[None]:
    """Saving ``local`` changes nothing for the deployment only if local is what it runs on.

    GET /storageConfig hides the stored type from every signed-in caller, so it is read
    from the key-value store, and the stored bytes are put back exactly afterwards.
    """
    before = read_stored_value(KV_STORAGE)
    storage_type = json.loads(before or b"{}").get("storageType")
    if storage_type != "local":
        pytest.fail(
            f"this deployment stores files in {storage_type!r}: saving a local storage "
            "configuration here would move every upload to another backend"
        )
    try:
        yield
    finally:
        if before is not None and read_stored_value(KV_STORAGE) != before:
            write_stored_value(KV_STORAGE, before)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(LOCAL_BODY, id="type-only"),
        # 'PipesHub' is the mount name the server falls back to when none is stored.
        pytest.param({**LOCAL_BODY, "mountName": "PipesHub"}, id="default-mount-name"),
    ],
)
@pytest.mark.usefixtures("local_storage_kept")
def test_create_storage_config_local_is_saved(config_client: ConfigClient, body: dict[str, Any]) -> None:
    resp = config_client.post(PATH, json=body)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    # The read side never shows a signed-in caller what was stored.
    read = config_client.get(PATH)
    assert read.status_code == 200, read.text[:500]
    assert read.json() == {}


@pytest.mark.usefixtures("local_storage_kept")
def test_create_storage_config_drops_what_the_validator_does_not_know(
    config_client: ConfigClient,
) -> None:
    with outside_request_contract("specAudit is not a field of the body; the validator strips it"):
        resp = config_client.post(PATH, json={**LOCAL_BODY, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_response(resp, ROUTE)


def test_create_storage_config_s3_that_fails_its_health_check_is_not_saved(
    config_client: ConfigClient,
) -> None:
    before = read_stored_value(KV_STORAGE)
    # Credentials no AWS account has: the live check fails at the first write, so the
    # stored configuration is never touched.
    body = {
        **S3_BODY,
        "s3BucketName": f"spec-audit-{uuid.uuid4().hex[:16]}",
        "s3AccessKeyId": "AKIASPECAUDITINVALID0",
        "s3SecretAccessKey": "spec-audit-not-a-real-secret-access-key",
    }

    resp = config_client.post(PATH, json=body, timeout=120)

    assert resp.status_code == 400, resp.text[:500]
    error = resp.json()["error"]
    assert error["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert error["message"].startswith("S3 health check failed."), resp.text[:500]
    checks = {check["capability"]: check for check in error["metadata"]["s3HealthCheck"]}
    assert checks["upload"]["passed"] is False
    assert checks["upload"]["error"]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert read_stored_value(KV_STORAGE) == before


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_create_storage_config_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=LOCAL_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_storage_config_as_member_is_forbidden(second_user: SecondUser) -> None:
    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=LOCAL_BODY)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.storageType"], id="empty-object"),
        # "gcp" is a storageTypes constant but not a member of the discriminated union.
        pytest.param({"storageType": "gcp"}, ["body.storageType"], id="unsupported-storage-type"),
        # The controller lower-cases the type, but the validator before it is case-sensitive.
        pytest.param({"storageType": "LOCAL"}, ["body.storageType"], id="storage-type-wrong-case"),
        pytest.param({**LOCAL_BODY, "baseUrl": "not-a-url"}, ["body.baseUrl"], id="local-base-url-not-a-url"),
        pytest.param({**LOCAL_BODY, "mountName": 42}, ["body.mountName"], id="local-mount-name-not-a-string"),
        pytest.param({"storageType": "s3", "s3Region": "us-east-1"}, ["body.s3BucketName"], id="s3-missing-bucket"),
        pytest.param({"storageType": "s3", "s3BucketName": "b"}, ["body.s3Region"], id="s3-missing-region"),
        pytest.param(
            {**S3_BODY, "s3Region": "", "s3BucketName": ""},
            ["body.s3Region", "body.s3BucketName"],
            id="s3-empty-region-and-bucket",
        ),
        # Refused by the zod refinement, before any S3 health check runs.
        pytest.param(
            {**S3_BODY, "s3AccessKeyId": "AKIASPECAUDIT"},
            ["body.s3SecretAccessKey"],
            id="s3-access-key-without-secret",
        ),
        pytest.param(
            {**S3_BODY, "s3SecretAccessKey": "spec-audit-secret"},
            ["body.s3AccessKeyId"],
            id="s3-secret-without-access-key",
        ),
        pytest.param(
            {k: v for k, v in AZURE_BODY.items() if k != "containerName"},
            ["body.containerName"],
            id="azure-missing-container",
        ),
        pytest.param(
            {**AZURE_BODY, "endpointProtocol": "ftp"},
            ["body.endpointProtocol"],
            id="azure-endpoint-protocol-not-http-or-https",
        ),
        pytest.param(
            {**AZURE_BODY, "accountName": "", "accountKey": "", "endpointSuffix": "", "containerName": ""},
            ["body.accountName", "body.accountKey", "body.endpointSuffix", "body.containerName"],
            id="azure-empty-strings",
        ),
        pytest.param([LOCAL_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_create_storage_config_rejects_invalid_body(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_storage_config_without_a_body_is_rejected_like_an_empty_one(
    config_client: ConfigClient,
) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.storageType")
    assert_strict_openapi_exchange(resp, ROUTE)
