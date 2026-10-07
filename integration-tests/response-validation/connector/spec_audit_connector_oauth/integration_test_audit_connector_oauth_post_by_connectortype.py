"""Strict OpenAPI audit of POST /api/v1/oauth/:connectorType."""

from __future__ import annotations

from typing import Any

import pytest
import requests
from connector_oauth_audit_support import (
    OAUTH_BASE,
    OTHER_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNKNOWN_CONNECTOR_TYPE,
    UNSAFE_PATH_SEGMENT,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    created_config_id,
    error_code,
    oauth_config_body,
    request_as,
    validation_error_fields,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType"


def _body_with(**overrides: Any) -> dict[str, Any]:
    return {**oauth_config_body(), **overrides}


def _body_without(field: str) -> dict[str, Any]:
    body = oauth_config_body()
    del body[field]
    return body


def _stored(client: ConnectorOAuthClient, connector_type: str, config_id: str) -> dict[str, Any]:
    """The admin's full view of one stored record, from the per-type listing."""
    resp = client.list_for_type(connector_type, limit=200)
    assert resp.status_code == 200, resp.text[:500]
    matches = [c for c in resp.json()["oauthConfigs"] if c["_id"] == config_id]
    assert len(matches) == 1, resp.text[:500]
    return matches[0]


def _delete_if_created(
    client: ConnectorOAuthClient, connector_type: str, resp: requests.Response
) -> None:
    config_id = created_config_id(resp)
    if config_id:
        client.remove(connector_type, config_id)


def test_create_returns_essential_fields_without_secrets(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    body = oauth_config_body()

    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        # Node relays FastAPI's default status, so a create answers 200 rather than 201.
        assert resp.status_code == 200, resp.text[:500]
        payload = resp.json()
        assert payload["success"] is True
        created = payload["oauthConfig"]
        assert created["_id"]
        assert created["oauthInstanceName"] == body["oauthInstanceName"]
        assert created["connectorType"] == SEED_CONNECTOR_TYPE
        assert "config" not in created
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


def test_surrounding_whitespace_is_trimmed_from_the_name(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    body = oauth_config_body()
    name = body["oauthInstanceName"]

    resp = connector_oauth_client.create(
        SEED_CONNECTOR_TYPE, {**body, "oauthInstanceName": f"  {name}\t "}
    )

    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        assert resp.json()["oauthConfig"]["oauthInstanceName"] == name
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize(
    "body",
    [_body_without("baseUrl"), _body_with(baseUrl="")],
    ids=["omitted", "empty"],
)
def test_create_without_base_url_builds_the_redirect_uri_from_the_frontend_endpoint(
    connector_oauth_client: ConnectorOAuthClient, body: dict[str, Any]
) -> None:
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        stored = _stored(connector_oauth_client, SEED_CONNECTOR_TYPE, resp.json()["oauthConfig"]["_id"])
        assert stored["redirectUri"].endswith(f"/connectors/oauth/callback/{SEED_CONNECTOR_TYPE}")
        assert stored["redirectUri"].startswith("http")
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


def test_base_url_is_prefixed_as_given_without_being_checked(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.create(
        SEED_CONNECTOR_TYPE, _body_with(baseUrl="spec-audit not a url")
    )

    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        stored = _stored(connector_oauth_client, SEED_CONNECTOR_TYPE, resp.json()["oauthConfig"]["_id"])
        assert stored["redirectUri"] == (
            f"spec-audit not a url/connectors/oauth/callback/{SEED_CONNECTOR_TYPE}"
        )
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


def test_config_keys_beyond_the_common_three_are_stored(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    # `config` is keyed by the type's authFields, which differ per connector type; none is validated.
    body = oauth_config_body(domain="spec-audit.example", useAdminConsent=True)

    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        stored = _stored(connector_oauth_client, SEED_CONNECTOR_TYPE, resp.json()["oauthConfig"]["_id"])
        assert stored["config"]["domain"] == "spec-audit.example"
        assert stored["config"]["useAdminConsent"] is True
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"extra": {"x": 1}}, id="nested-object"),
        pytest.param({"extra": None}, id="null-value"),
        pytest.param({"clientSecret": None}, id="client-secret-null"),
        pytest.param({"clientId": 123}, id="client-id-number"),
        pytest.param({"extra": [1, 2]}, id="array-of-numbers"),
    ],
)
def test_config_values_are_stored_without_type_checks(
    connector_oauth_client: ConnectorOAuthClient, extra: dict[str, Any]
) -> None:
    body = oauth_config_body(**extra)

    with outside_request_contract("config values are typed in the spec; Python stores any JSON value"):
        resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)
        assert_strict_openapi_exchange(resp, ROUTE)

    try:
        assert resp.status_code == 200, resp.text[:500]
        stored = _stored(connector_oauth_client, SEED_CONNECTOR_TYPE, resp.json()["oauthConfig"]["_id"])
        assert stored["config"] == body["config"]
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


def test_unknown_body_field_is_dropped(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    with outside_request_contract("Node forwards only oauthInstanceName, config and baseUrl"):
        resp = connector_oauth_client.create(
            SEED_CONNECTOR_TYPE, _body_with(connectorScope="personal", specAudit="x")
        )
        assert_strict_openapi_exchange(resp, ROUTE)

    try:
        assert resp.status_code == 200, resp.text[:500]
        stored = _stored(connector_oauth_client, SEED_CONNECTOR_TYPE, resp.json()["oauthConfig"]["_id"])
        assert "specAudit" not in stored
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize(
    "connector_type",
    [UNKNOWN_CONNECTOR_TYPE, SEED_CONNECTOR_TYPE.lower()],
    ids=["unregistered", "wrong-case"],
)
def test_create_for_a_type_outside_the_registry_is_stored_anyway(
    connector_oauth_client: ConnectorOAuthClient, connector_type: str
) -> None:
    # API bug: nothing checks the type against the registry, so a typo creates an unusable config.
    resp = connector_oauth_client.create(connector_type, oauth_config_body())

    try:
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
        created = resp.json()["oauthConfig"]
        assert created["connectorType"] == connector_type
        assert created["iconPath"] == "/icons/connectors/default.svg"
        assert created["appGroup"] == ""
        assert created["appCategories"] == []
        # Listed under the name it was created with, and (see the GET /oauth audit) nowhere else.
        # With no registry entry there are no provider URLs or scopes, and no redirectUri is built.
        stored = _stored(connector_oauth_client, connector_type, created["_id"])
        assert stored["config"]["clientId"] == "spec-audit-client-id"
        for absent in ("redirectUri", "authorizeUrl", "tokenUrl", "scopes"):
            assert absent not in stored, stored
    finally:
        _delete_if_created(connector_oauth_client, connector_type, resp)


def test_create_without_token_is_unauthorized(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, oauth_config_body(), auth=False)

    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_as_member_is_forbidden(second_user: Any) -> None:
    # Node has no admin gate here; the refusal is Python's, relayed through handleBackendError.
    resp = request_as(second_user, "POST", f"/{SEED_CONNECTOR_TYPE}", json=oauth_config_body())

    try:
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        # Only reachable if the gate is missing; the member cannot delete, the admin can.
        config_id = created_config_id(resp)
        if config_id:
            pytest.fail(f"member created OAuth config {config_id}; delete it as admin")


def test_create_without_connector_write_scope_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "POST",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}",
        auth=False,
        headers=bearer(token_without_connector_read),
        json=oauth_config_body(),
    )

    assert resp.status_code == 403, resp.text[:500]
    assert "connector:write" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param(_body_without("oauthInstanceName"), "body.oauthInstanceName", id="name-missing"),
        pytest.param(_body_with(oauthInstanceName=""), "body.oauthInstanceName", id="name-empty"),
        # Strings are trimmed before validation, so blanks alone count as empty.
        pytest.param(_body_with(oauthInstanceName="   "), "body.oauthInstanceName", id="name-blank"),
        pytest.param(_body_with(oauthInstanceName=5), "body.oauthInstanceName", id="name-number"),
        pytest.param(_body_with(oauthInstanceName=None), "body.oauthInstanceName", id="name-null"),
        pytest.param(_body_with(baseUrl=5), "body.baseUrl", id="base-url-number"),
        pytest.param(_body_with(baseUrl=None), "body.baseUrl", id="base-url-null"),
        pytest.param({}, "body.oauthInstanceName", id="empty-object"),
        pytest.param([oauth_config_body()], "body", id="array"),
        pytest.param(None, "body.oauthInstanceName", id="no-body"),
    ],
)
def test_validator_refuses_body(
    connector_oauth_client: ConnectorOAuthClient, body: Any, field: str
) -> None:
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        assert resp.status_code == 400, resp.text[:500]
        assert validation_error_fields(resp) == [field], resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        # Node's own check: the validator takes `config` as anything, the controller wants it truthy.
        pytest.param(_body_without("config"), "Config is required", id="config-missing"),
        pytest.param(_body_with(config=None), "Config is required", id="config-null"),
        pytest.param(_body_with(config=False), "Config is required", id="config-false"),
        pytest.param(_body_with(config=0), "Config is required", id="config-zero"),
        # Truthy for Node, empty for Python.
        pytest.param(_body_with(config={}), "config is required", id="config-empty-object"),
        pytest.param(_body_with(config=[]), "config is required", id="config-empty-array"),
    ],
)
def test_unusable_config_is_a_bad_request(
    connector_oauth_client: ConnectorOAuthClient, body: dict[str, Any], message: str
) -> None:
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, body)

    try:
        assert resp.status_code == 400, resp.text[:500]
        assert error_code(resp) == "HTTP_BAD_REQUEST"
        assert resp.json()["error"]["message"] == message
        assert_strict_openapi_exchange(resp, ROUTE)
        assert_spec_forbids_request(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize("config", ["client-id-as-text", ["clientId"], 5], ids=["string", "array", "number"])
def test_config_that_is_not_an_object_is_an_internal_error(
    connector_oauth_client: ConnectorOAuthClient, config: Any
) -> None:
    # API bug: Python walks `config` as a dict without checking, and the crash surfaces as 500.
    resp = connector_oauth_client.create(SEED_CONNECTOR_TYPE, _body_with(config=config))

    try:
        assert resp.status_code == 500, resp.text[:500]
        assert error_code(resp) == "HTTP_INTERNAL_SERVER_ERROR"
        assert_strict_openapi_exchange(resp, ROUTE)
        assert_spec_forbids_request(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, SEED_CONNECTOR_TYPE, resp)


@pytest.mark.parametrize("connector_type", ["Salesforce", "salesforce"], ids=["registered", "lower-case"])
def test_salesforce_login_url_off_salesforce_is_a_bad_request(
    connector_oauth_client: ConnectorOAuthClient, connector_type: str
) -> None:
    # The token request would send the client secret to loginUrl, so only Salesforce hosts pass.
    resp = connector_oauth_client.create(
        connector_type, oauth_config_body(loginUrl="https://spec-audit.example")
    )

    try:
        assert resp.status_code == 400, resp.text[:500]
        assert error_code(resp) == "HTTP_BAD_REQUEST"
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, connector_type, resp)


def test_create_with_duplicate_name_is_conflict(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    existing = seed_oauth_config()

    resp = connector_oauth_client.create(
        existing["connector_type"], oauth_config_body(existing["name"])
    )

    try:
        assert resp.status_code == 409, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    finally:
        _delete_if_created(connector_oauth_client, existing["connector_type"], resp)


def test_same_name_under_another_type_is_not_a_conflict(
    connector_oauth_client: ConnectorOAuthClient,
    seed_oauth_config: SeedOAuthConfig,
) -> None:
    existing = seed_oauth_config()

    other = seed_oauth_config(OTHER_CONNECTOR_TYPE, existing["name"])

    assert other["name"] == existing["name"]
    assert other["id"] != existing["id"]


def test_create_rejects_unsafe_connector_type_before_auth(
    connector_oauth_client: ConnectorOAuthClient,
) -> None:
    resp = connector_oauth_client.create(UNSAFE_PATH_SEGMENT, oauth_config_body(), auth=False)

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
