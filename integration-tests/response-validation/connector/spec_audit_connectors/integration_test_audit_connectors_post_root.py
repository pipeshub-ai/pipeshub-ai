"""Strict OpenAPI audit of POST /api/v1/connectors."""

from __future__ import annotations

from typing import Any

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_TYPE,
    PERSONAL_OAUTH_AUTH_TYPE,
    SEED_CONNECTOR_TYPE,
    ConnectorsAuditClient,
    bearer,
    created_connector_id,
    personal_body,
    request_as,
    seed_body,
)
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors"


def test_admin_creates_a_team_instance(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    body = seed_body()

    resp = connectors_client.create_instance(**body)
    # API quirk: a create answers 200, not 201.
    assert resp.status_code == 200, resp.text[:500]
    cleanup_connectors.append(created_connector_id(resp))
    assert_strict_openapi_exchange(resp, ROUTE)

    payload = resp.json()
    assert payload["success"] is True
    assert payload["message"] == "Connector instance created successfully."
    created = payload["connector"]
    assert created["connectorType"] == SEED_CONNECTOR_TYPE
    assert created["instanceName"] == body["instanceName"]
    assert created["scope"] == "team"
    assert created["created"] is True
    assert created["isAuthenticated"] is False
    assert created["isConfigured"] is False

    stored = connectors_client.get(f"/{created['connectorId']}")
    assert stored.status_code == 200, stored.text[:500]
    instance = stored.json()["connector"]
    assert instance["_key"] == created["connectorId"]
    assert instance["name"] == body["instanceName"]
    assert instance["createdBy"] == created["createdBy"]
    assert instance["isActive"] is False


def test_create_trims_the_name_and_picks_the_first_auth_type(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    body = seed_body()
    name = body["instanceName"]
    body["instanceName"] = f"  {name}  "
    del body["authType"]

    resp = connectors_client.create_instance(**body)
    assert resp.status_code == 200, resp.text[:500]
    connector_id = created_connector_id(resp)
    cleanup_connectors.append(connector_id)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connector"]["instanceName"] == name

    stored = connectors_client.get(f"/{connector_id}").json()["connector"]
    assert stored["name"] == name
    assert stored["authType"] == stored["supportedAuthTypes"][0]


def test_create_with_initial_config_reports_configured(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    resp = connectors_client.create_instance(
        **seed_body(
            config={
                "sync": {"selectedStrategy": "MANUAL"},
                "filters": {"sync": {"values": {}}, "indexing": {"values": {}}},
            }
        )
    )
    assert resp.status_code == 200, resp.text[:500]
    cleanup_connectors.append(created_connector_id(resp))
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connector"]["isConfigured"] is True


def test_create_accepts_a_long_name_and_an_unchecked_base_url(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    # Neither the gateway nor the connector service bounds the name or parses the URL.
    body = seed_body(baseUrl="not a url")
    body["instanceName"] = body["instanceName"] + " " + "x" * 300

    resp = connectors_client.create_instance(**body)
    assert resp.status_code == 200, resp.text[:500]
    cleanup_connectors.append(created_connector_id(resp))
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["connector"]["instanceName"] == body["instanceName"]


def test_member_creates_a_personal_instance(
    second_user: SecondUser, cleanup_connectors: list[str]
) -> None:
    body = personal_body()

    resp = request_as(second_user, "POST", "/", json=body)
    assert resp.status_code == 200, resp.text[:500]
    cleanup_connectors.append(created_connector_id(resp))
    assert_strict_openapi_exchange(resp, ROUTE)

    created = resp.json()["connector"]
    assert created["scope"] == "personal"
    assert created["createdBy"] == second_user.user_id
    assert created["connectorType"] == body["connectorType"]


def test_create_drops_fields_it_does_not_know(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    with outside_request_contract("the validator strips body fields it does not know"):
        resp = connectors_client.create_instance(
            **seed_body(specAuditUnknown=1, config={"specAuditUnknown": {"a": 1}})
        )
        assert resp.status_code == 200, resp.text[:500]
        cleanup_connectors.append(created_connector_id(resp))
        assert_strict_openapi_exchange(resp, ROUTE)
    # The unknown config key was removed before the handler ran, leaving nothing to save.
    assert resp.json()["connector"]["isConfigured"] is False


def _without(field: str) -> dict[str, Any]:
    body = seed_body()
    del body[field]
    return body


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-object"),
        pytest.param(_without("connectorType"), id="connector-type-missing"),
        pytest.param(_without("instanceName"), id="instance-name-missing"),
        pytest.param(_without("scope"), id="scope-missing"),
        pytest.param(seed_body(connectorType=""), id="connector-type-empty"),
        pytest.param(seed_body(connectorType=7), id="connector-type-not-a-string"),
        pytest.param(seed_body(instanceName=""), id="instance-name-empty"),
        pytest.param(seed_body(instanceName=["a"]), id="instance-name-not-a-string"),
        pytest.param(seed_body(scope="organisation"), id="scope-not-in-enum"),
        pytest.param(seed_body(scope="TEAM"), id="scope-wrong-case"),
        pytest.param(seed_body(scope=None), id="scope-null"),
        pytest.param(seed_body(authType=5), id="auth-type-not-a-string"),
        pytest.param(seed_body(baseUrl=5), id="base-url-not-a-string"),
        pytest.param(seed_body(config="auth"), id="config-not-an-object"),
        pytest.param(seed_body(config=[]), id="config-an-array"),
    ],
)
def test_create_with_an_invalid_body_is_rejected_by_the_validator(
    connectors_client: ConnectorsAuditClient, body: dict[str, Any]
) -> None:
    resp = connectors_client.create_instance(**body)
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_blank_name_is_refused_by_the_connector_service(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # Passes the validator's min length, then is trimmed to nothing in Python.
    resp = connectors_client.create_instance(**seed_body(instanceName="   "))
    assert resp.status_code == 400, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_BAD_REQUEST", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param({"scope": "personal"}, "does not support scope", id="scope-not-offered-by-type"),
        pytest.param({"authType": "OAUTH"}, "is not supported", id="auth-type-not-offered-by-type"),
    ],
)
def test_create_the_type_does_not_allow_is_bad_request(
    connectors_client: ConnectorsAuditClient, overrides: dict[str, str], message: str
) -> None:
    resp = connectors_client.create_instance(**seed_body(**overrides))
    assert resp.status_code == 400, resp.text[:500]
    assert message in resp.json()["error"]["message"], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_with_a_name_already_in_use_is_bad_request(
    connectors_client: ConnectorsAuditClient, cleanup_connectors: list[str]
) -> None:
    body = seed_body()
    first = connectors_client.create_instance(**body)
    cleanup_connectors.append(created_connector_id(first))

    resp = connectors_client.create_instance(**body)
    # API quirk: a name clash is a 400, not a 409.
    assert resp.status_code == 400, resp.text[:500]
    assert "already used" in resp.json()["error"]["message"], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_of_an_unregistered_type_is_not_found(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.create_instance(**seed_body(connectorType=MISSING_CONNECTOR_TYPE))
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_create_a_team_instance(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/", json=seed_body())
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_oauth_instance_needs_an_oauth_app(second_user: SecondUser) -> None:
    resp = request_as(
        second_user, "POST", "/", json=personal_body(authType=PERSONAL_OAUTH_AUTH_TYPE)
    )
    assert resp.status_code == 400, resp.text[:500]
    assert "OAuth App selection is required" in resp.json()["error"]["message"], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_top_level_oauth_config_id_is_discarded(second_user: SecondUser) -> None:
    # API bug: the gateway forwards a fixed list of body fields and oauthConfigId is not
    # one of them, so Python never sees it and answers as if no OAuth App was selected.
    # The same id under config.auth is honoured (next test).
    with outside_request_contract("oauthConfigId at the top level is dropped by the gateway"):
        resp = request_as(
            second_user,
            "POST",
            "/",
            json=personal_body(authType=PERSONAL_OAUTH_AUTH_TYPE, oauthConfigId="spec-audit-no-such-app"),
        )
        assert resp.status_code == 400, resp.text[:500]
        assert "OAuth App selection is required" in resp.json()["error"]["message"], resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_member_selecting_an_unknown_oauth_app_is_not_found(second_user: SecondUser) -> None:
    resp = request_as(
        second_user,
        "POST",
        "/",
        json=personal_body(
            authType=PERSONAL_OAUTH_AUTH_TYPE,
            config={"auth": {"oauthConfigId": "spec-audit-no-such-app"}},
        ),
    )
    assert resp.status_code == 404, resp.text[:500]
    assert "OAuth App not found" in resp.json()["error"]["message"], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_supply_oauth_credentials(second_user: SecondUser) -> None:
    with outside_request_contract("OAuth client credentials are connector-specific auth fields"):
        resp = request_as(
            second_user,
            "POST",
            "/",
            json=personal_body(
                authType=PERSONAL_OAUTH_AUTH_TYPE,
                config={"auth": {"clientId": "spec-audit", "clientSecret": "spec-audit"}},
            ),
        )
        assert resp.status_code == 403, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_a_token_is_unauthorized(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.create_instance(auth=False, **seed_body())
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_without_the_connector_write_scope_is_forbidden(
    connectors_client: ConnectorsAuditClient, token_without_connector_scopes: str
) -> None:
    resp = connectors_client.post(
        "/", auth=False, headers=bearer(token_without_connector_scopes), json=seed_body()
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
