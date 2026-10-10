"""Strict OpenAPI audit of POST /api/v1/configurationManager/platform/settings.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> zod body -> setPlatformSettings.
The POST replaces the whole stored object, and other suites depend on the flags and the upload
limit while these tests run: every case that stores something else posts the previous values
back at once, and none ever lowers the upload limit.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import (
    INVALID_BEARER_HEADERS,
    assert_validation_error,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/platform/settings"
PATH = "/platform/settings"
SAVED_BODY = {"message": "Platform settings saved"}

MAX_UPLOAD_BYTES = 1024**3
# Never stored: only bodies the validator or an earlier middleware refuses are built from it.
UNUSED_BODY: dict[str, Any] = {"fileUploadMaxSizeBytes": 31457280, "featureFlags": {}}
SPEC_AUDIT_FLAG = "SPEC_AUDIT_UNKNOWN_FLAG"


def _read(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, f"could not read platform settings: {resp.text[:500]}"
    body: dict[str, Any] = resp.json()
    return body


@pytest.fixture
def current(config_client: ConfigClient) -> Iterator[dict[str, Any]]:
    """The settings in force; posted back if a test left anything else behind."""
    before = _read(config_client)
    body = {
        "fileUploadMaxSizeBytes": before["fileUploadMaxSizeBytes"],
        "featureFlags": before["featureFlags"],
    }
    try:
        yield body
    finally:
        if _read(config_client) != before:
            restored = config_client.post(PATH, json=body)
            assert restored.status_code == 200, f"platform settings not restored: {restored.text[:500]}"
            assert _read(config_client) == before


def test_set_platform_settings_with_current_values_is_saved(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    resp = config_client.post(PATH, json=current)

    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _read(config_client) == current


def test_set_platform_settings_stores_the_largest_limit_and_a_flag_nobody_defined(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    body = {
        "fileUploadMaxSizeBytes": MAX_UPLOAD_BYTES,
        "featureFlags": {**current["featureFlags"], SPEC_AUDIT_FLAG: True},
    }

    resp = config_client.post(PATH, json=body)
    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        # Flag names are not checked against the known ones: any name is stored and returned.
        assert _read(config_client) == body
    finally:
        restored = config_client.post(PATH, json=current)
    assert restored.status_code == 200, restored.text[:500]
    # The replace is whole: the extra flag is gone once a body without it is saved.
    assert _read(config_client) == current


def test_set_platform_settings_without_feature_flags_resets_them_to_their_defaults(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    available = config_client.get("/platform/feature-flags/available")
    assert available.status_code == 200, available.text[:500]
    defaults = {flag["key"]: flag["defaultEnabled"] for flag in available.json()["flags"]}
    assert defaults, "the server lists no feature flags to compare defaults with"
    custom = {**current["featureFlags"], SPEC_AUDIT_FLAG: True}
    seeded = config_client.post(PATH, json={**current, "featureFlags": custom})
    assert seeded.status_code == 200, seeded.text[:500]

    resp = config_client.post(PATH, json={"fileUploadMaxSizeBytes": current["fileUploadMaxSizeBytes"]})
    try:
        after = _read(config_client)
    finally:
        # Other suites need the flags that were on (MCP is off by default): put them back first.
        restored = config_client.post(PATH, json=current)

    assert restored.status_code == 200, restored.text[:500]
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json() == SAVED_BODY
    assert_strict_openapi_exchange(resp, ROUTE)
    # Left out means an empty map was stored: the unknown flag is gone and the known ones
    # read as their built-in defaults again.
    assert SPEC_AUDIT_FLAG not in after["featureFlags"]
    assert set(after["featureFlags"]) == set(current["featureFlags"])
    assert {key: after["featureFlags"][key] for key in defaults} == defaults
    assert after["fileUploadMaxSizeBytes"] == current["fileUploadMaxSizeBytes"]


def test_set_platform_settings_drops_what_the_validator_does_not_know(
    config_client: ConfigClient, current: dict[str, Any]
) -> None:
    sent = {**current, "updatedAt": "2001-01-01T00:00:00.000Z", "specAudit": "x"}

    with outside_request_contract("updatedAt and specAudit are not fields of the body"):
        resp = config_client.post(PATH, json=sent)

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _read(config_client) == current


@pytest.mark.parametrize(
    "headers",
    [None, INVALID_BEARER_HEADERS],
    ids=["no-token", "invalid-token"],
)
def test_set_platform_settings_without_valid_token_is_unauthorized(
    config_client: ConfigClient, headers: dict[str, str] | None
) -> None:
    resp = config_client.post(PATH, auth=False, headers=headers, json=UNUSED_BODY)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_set_platform_settings_as_member_is_forbidden(
    config_client: ConfigClient, second_user: SecondUser
) -> None:
    before = _read(config_client)

    # A valid body, so the 403 can only come from userAdminCheck, which runs before zod.
    resp = request_as(second_user, "POST", PATH, json=UNUSED_BODY)

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _read(config_client) == before


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        pytest.param({}, ["body.fileUploadMaxSizeBytes"], id="empty-object"),
        pytest.param({"featureFlags": {}}, ["body.fileUploadMaxSizeBytes"], id="missing-upload-limit"),
        # One byte over the 1 GB ceiling of platformSettingsSchema.
        pytest.param(
            {**UNUSED_BODY, "fileUploadMaxSizeBytes": MAX_UPLOAD_BYTES + 1},
            ["body.fileUploadMaxSizeBytes"],
            id="upload-limit-over-1gb",
        ),
        pytest.param({**UNUSED_BODY, "fileUploadMaxSizeBytes": 0}, ["body.fileUploadMaxSizeBytes"], id="upload-limit-zero"),
        pytest.param({**UNUSED_BODY, "fileUploadMaxSizeBytes": -1}, ["body.fileUploadMaxSizeBytes"], id="upload-limit-negative"),
        pytest.param(
            {**UNUSED_BODY, "fileUploadMaxSizeBytes": 1048576.5},
            ["body.fileUploadMaxSizeBytes"],
            id="upload-limit-not-whole",
        ),
        pytest.param(
            {**UNUSED_BODY, "fileUploadMaxSizeBytes": "31457280"},
            ["body.fileUploadMaxSizeBytes"],
            id="upload-limit-as-string",
        ),
        pytest.param(
            {**UNUSED_BODY, "featureFlags": {"ENABLE_BETA_CONNECTORS": "true"}},
            ["body.featureFlags.ENABLE_BETA_CONNECTORS"],
            id="flag-value-not-boolean",
        ),
        pytest.param({**UNUSED_BODY, "featureFlags": ["ENABLE_MCP"]}, ["body.featureFlags"], id="flags-as-list"),
        # Optional means "may be left out": an explicit null is refused.
        pytest.param({**UNUSED_BODY, "featureFlags": None}, ["body.featureFlags"], id="flags-null"),
        pytest.param([UNUSED_BODY], ["body"], id="body-is-a-list"),
    ],
)
def test_set_platform_settings_rejects_invalid_body(
    config_client: ConfigClient, body: Any, fields: list[str]
) -> None:
    before = _read(config_client)

    resp = config_client.post(PATH, json=body)

    assert_validation_error(resp, *fields)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _read(config_client) == before


def test_set_platform_settings_without_a_body_is_rejected_like_an_empty_one(
    config_client: ConfigClient,
) -> None:
    resp = config_client.post(PATH)

    assert_validation_error(resp, "body.fileUploadMaxSizeBytes")
    assert_strict_openapi_exchange(resp, ROUTE)
