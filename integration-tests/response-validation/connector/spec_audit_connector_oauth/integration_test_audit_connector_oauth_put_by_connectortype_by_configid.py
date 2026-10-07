"""Strict OpenAPI audit of PUT /api/v1/oauth/:connectorType/:configId."""

from __future__ import annotations

from typing import Any

import pytest
from connector_oauth_audit_support import (
    MISSING_CONFIG_ID,
    OAUTH_BASE,
    OTHER_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_PATH_SEGMENT,
    ConnectorOAuthClient,
    SeedOAuthConfig,
    bearer,
    error_code,
    request_as,
    unique_name,
    validation_error_fields,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/oauth/:connectorType/:configId"


def _stored_config(client: ConnectorOAuthClient, cfg: Any) -> dict[str, Any]:
    resp = client.fetch(cfg["connector_type"], cfg["id"])
    assert resp.status_code == 200, resp.text[:500]
    return resp.json()["oauthConfig"]


def test_admin_renames_config_and_merges_new_fields(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()
    new_name = unique_name()

    resp = connector_oauth_client.update(
        cfg["connector_type"],
        cfg["id"],
        {"oauthInstanceName": new_name, "config": {"clientId": "spec-audit-client-id-2"}},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    body = resp.json()
    assert body["success"] is True
    updated = body["oauthConfig"]
    assert updated["_id"] == cfg["id"]
    assert updated["oauthInstanceName"] == new_name
    assert updated["connectorType"] == cfg["connector_type"]
    # The reply carries only the essential fields: secrets never come back from an update.
    assert "config" not in updated

    stored = _stored_config(connector_oauth_client, cfg)
    assert stored["config"]["clientId"] == "spec-audit-client-id-2"
    assert stored["config"]["clientSecret"] == cfg["body"]["config"]["clientSecret"]


def test_config_alone_is_merged_and_the_name_is_kept(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(
        cfg["connector_type"], cfg["id"], {"config": {"tenantId": "spec-audit-tenant", "domain": "d.example"}}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["oauthConfig"]["oauthInstanceName"] == cfg["name"]

    stored = _stored_config(connector_oauth_client, cfg)
    assert stored["config"] == {
        **cfg["body"]["config"],
        "tenantId": "spec-audit-tenant",
        "domain": "d.example",
    }


def test_empty_config_object_alone_is_accepted_and_changes_nothing(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    # `{}` is truthy for Node's "name or config" check and falsy for Python's merge.
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(cfg["connector_type"], cfg["id"], {"config": {}})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["oauthConfig"]["oauthInstanceName"] == cfg["name"]
    assert _stored_config(connector_oauth_client, cfg)["config"] == cfg["body"]["config"]


@pytest.mark.parametrize("config", [None, False, 0, ""], ids=["null", "false", "zero", "empty-string"])
def test_falsy_config_next_to_a_name_is_ignored(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig, config: Any
) -> None:
    # Node forwards `config` only when it is truthy, so the rename goes through on its own.
    cfg = seed_oauth_config()
    new_name = unique_name()

    with outside_request_contract("config is typed object; a falsy value is dropped by Node"):
        resp = connector_oauth_client.update(
            cfg["connector_type"], cfg["id"], {"oauthInstanceName": new_name, "config": config}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.json()["oauthConfig"]["oauthInstanceName"] == new_name
    stored = _stored_config(connector_oauth_client, cfg)
    assert stored["oauthInstanceName"] == new_name
    assert stored["config"] == cfg["body"]["config"]


def test_empty_array_config_alone_is_accepted_and_changes_nothing(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    # `[]` is truthy for Node's "name or config" check and falsy for Python's merge.
    cfg = seed_oauth_config()

    with outside_request_contract("config is typed object; an empty array passes as if it were {}"):
        resp = connector_oauth_client.update(cfg["connector_type"], cfg["id"], {"config": []})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)

    assert resp.json()["oauthConfig"]["oauthInstanceName"] == cfg["name"]
    stored = _stored_config(connector_oauth_client, cfg)
    assert stored["oauthInstanceName"] == cfg["name"]
    assert stored["config"] == cfg["body"]["config"]


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
def test_config_values_are_merged_without_type_checks(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig, extra: dict[str, Any]
) -> None:
    cfg = seed_oauth_config()

    with outside_request_contract("config values are typed in the spec; Python stores any JSON value"):
        resp = connector_oauth_client.update(cfg["connector_type"], cfg["id"], {"config": extra})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)

    assert _stored_config(connector_oauth_client, cfg)["config"] == {**cfg["body"]["config"], **extra}


def test_base_url_does_not_replace_an_existing_redirect_uri(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(
        cfg["connector_type"],
        cfg["id"],
        {"oauthInstanceName": cfg["name"], "baseUrl": "https://spec-audit-other.example"},
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)

    listed = connector_oauth_client.list_for_type(cfg["connector_type"], search=cfg["name"], limit=200)
    record = next(c for c in listed.json()["oauthConfigs"] if c["_id"] == cfg["id"])
    assert record["redirectUri"].startswith(cfg["body"]["baseUrl"])


def test_unknown_body_field_is_dropped(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    with outside_request_contract("Node forwards only oauthInstanceName, config and baseUrl"):
        resp = connector_oauth_client.update(
            cfg["connector_type"], cfg["id"], {"oauthInstanceName": cfg["name"], "specAudit": "x"}
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_rename_to_a_name_already_taken_is_a_conflict(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    taken = seed_oauth_config()
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(
        cfg["connector_type"], cfg["id"], {"oauthInstanceName": taken["name"]}
    )
    assert resp.status_code == 409, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_salesforce_login_url_off_salesforce_is_a_bad_request_and_changes_nothing(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config("Salesforce")

    resp = connector_oauth_client.update(
        cfg["connector_type"], cfg["id"], {"config": {"loginUrl": "https://spec-audit.example"}}
    )

    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "loginUrl" not in _stored_config(connector_oauth_client, cfg)["config"]


def test_unknown_config_id_is_not_found(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.update(
        SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, {"oauthInstanceName": unique_name()}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_config_under_another_connector_type_is_not_found(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(
        OTHER_CONNECTOR_TYPE, cfg["id"], {"oauthInstanceName": unique_name()}
    )
    assert resp.status_code == 404, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _stored_config(connector_oauth_client, cfg)["oauthInstanceName"] == cfg["name"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"baseUrl": "http://localhost:3001"}, id="base-url-only"),
        pytest.param({"config": None}, id="config-null"),
        pytest.param({"config": False}, id="config-false"),
        pytest.param({"config": 0}, id="config-zero"),
        pytest.param({"config": ""}, id="config-empty-string"),
        pytest.param(None, id="no-body"),
    ],
)
def test_body_with_neither_name_nor_config_is_rejected(
    connector_oauth_client: ConnectorOAuthClient, body: Any
) -> None:
    # Every body field is optional in the zod schema; the controller refuses this before proxying.
    resp = connector_oauth_client.update(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, body)
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        pytest.param({"oauthInstanceName": ""}, "body.oauthInstanceName", id="name-empty"),
        # Strings are trimmed before validation, so blanks alone count as empty.
        pytest.param({"oauthInstanceName": "   "}, "body.oauthInstanceName", id="name-blank"),
        pytest.param({"oauthInstanceName": 5}, "body.oauthInstanceName", id="name-number"),
        pytest.param(
            {"oauthInstanceName": None, "config": {"clientId": "x"}},
            "body.oauthInstanceName",
            id="name-null",
        ),
        pytest.param({"oauthInstanceName": "spec-audit", "baseUrl": 5}, "body.baseUrl", id="base-url-number"),
        pytest.param([{"oauthInstanceName": "spec-audit"}], "body", id="array"),
    ],
)
def test_validator_refuses_body(
    connector_oauth_client: ConnectorOAuthClient, body: Any, field: str
) -> None:
    resp = connector_oauth_client.update(SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, body)
    assert resp.status_code == 400, resp.text[:500]
    assert validation_error_fields(resp) == [field], resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize("config", ["client-id-as-text", ["clientId"], 5], ids=["string", "array", "number"])
def test_config_that_is_not_an_object_is_an_internal_error(
    connector_oauth_client: ConnectorOAuthClient, seed_oauth_config: SeedOAuthConfig, config: Any
) -> None:
    # API bug: Python merges `config` into the stored dict without checking its type.
    cfg = seed_oauth_config()

    resp = connector_oauth_client.update(cfg["connector_type"], cfg["id"], {"config": config})
    assert resp.status_code == 500, resp.text[:500]
    assert error_code(resp) == "HTTP_INTERNAL_SERVER_ERROR"
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert _stored_config(connector_oauth_client, cfg)["config"] == cfg["body"]["config"]


@pytest.mark.parametrize(
    ("connector_type", "config_id"),
    [(SEED_CONNECTOR_TYPE, UNSAFE_PATH_SEGMENT), (UNSAFE_PATH_SEGMENT, MISSING_CONFIG_ID)],
    ids=["config-id", "connector-type"],
)
def test_unsafe_path_segment_is_rejected_before_auth(
    connector_oauth_client: ConnectorOAuthClient, connector_type: str, config_id: str
) -> None:
    resp = connector_oauth_client.update(
        connector_type, config_id, {"oauthInstanceName": unique_name()}, auth=False
    )
    assert resp.status_code == 400, resp.text[:500]
    assert error_code(resp) == "HTTP_BAD_REQUEST"
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(connector_oauth_client: ConnectorOAuthClient) -> None:
    resp = connector_oauth_client.update(
        SEED_CONNECTOR_TYPE, MISSING_CONFIG_ID, {"oauthInstanceName": unique_name()}, auth=False
    )
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_token_without_connector_write_is_forbidden(
    pipeshub_client: PipeshubClient, token_without_connector_read: str
) -> None:
    resp = pipeshub_client.request(
        "PUT",
        f"{OAUTH_BASE}/{SEED_CONNECTOR_TYPE}/{MISSING_CONFIG_ID}",
        auth=False,
        headers=bearer(token_without_connector_read),
        json={"oauthInstanceName": unique_name()},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert "connector:write" in resp.json()["error"]["message"]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_cannot_update(
    second_user: SecondUser, seed_oauth_config: SeedOAuthConfig
) -> None:
    cfg = seed_oauth_config()

    # Node has no admin gate here; the 403 is Python's, relayed by handleBackendError.
    resp = request_as(
        second_user,
        "PUT",
        f"/{cfg['connector_type']}/{cfg['id']}",
        json={"oauthInstanceName": unique_name()},
    )
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
