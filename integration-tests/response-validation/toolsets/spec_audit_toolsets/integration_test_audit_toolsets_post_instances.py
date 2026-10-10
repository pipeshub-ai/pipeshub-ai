"""Strict OpenAPI audit of POST /api/v1/toolsets/instances."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import requests
from strict_openapi import (
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)
from toolsets_audit_support import (
    MISSING_OAUTH_CONFIG_ID,
    MISSING_TOOLSET_TYPE,
    OAUTH_CLIENT_AUTH,
    OAUTH_ONLY_LOCAL_TOOLSET_TYPE,
    TOOLSET_TYPE,
    DeleteInstanceLater,
    JsonObject,
    SeedToolsetInstance,
    ToolsetsClient,
    assert_not_found,
    error_of,
    instance_body,
    oauth_instance_body,
    rejected_fields,
    request_as,
    toolset_store_lock,
)

from helper.second_user import SecondUser

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/toolsets/instances"
MASK = "\u2022" * 8
STORED_FIELDS = {
    "_id",
    "instanceName",
    "toolsetType",
    "authType",
    "orgId",
    "createdBy",
    "createdAtTimestamp",
    "updatedAtTimestamp",
}


@pytest.fixture
def create(toolsets_client: ToolsetsClient, delete_instance_later: DeleteInstanceLater) -> Any:
    """POST /instances as admin; whatever it creates is deleted on teardown."""

    def _create(body: JsonObject) -> requests.Response:
        with toolset_store_lock():
            resp = toolsets_client.create_instance(body)
        if resp.status_code < 300:
            delete_instance_later(resp.json()["instance"])
        return resp

    return _create


def _created(resp: requests.Response) -> JsonObject:
    # The Python handler sets no status code, and Node forwards FastAPI's 200.
    assert resp.status_code == 200, resp.text[:500]
    body = resp.json()
    assert (body["status"], body["message"]) == ("success", "Toolset instance created successfully.")
    return body["instance"]


def test_admin_creates_an_api_token_instance(create: Any) -> None:
    body = instance_body()

    resp = create(body)
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(instance) == STORED_FIELDS, sorted(instance)
    assert instance["instanceName"] == body["instanceName"]
    assert (instance["toolsetType"], instance["authType"]) == (TOOLSET_TYPE, "API_TOKEN")
    assert instance["createdAtTimestamp"] == instance["updatedAtTimestamp"]


def test_create_normalises_the_toolset_type_and_auth_type(create: Any) -> None:
    resp = create(instance_body(toolsetType=TOOLSET_TYPE.upper(), authType="api_token"))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (instance["toolsetType"], instance["authType"]) == (TOOLSET_TYPE, "API_TOKEN")


def test_create_accepts_the_auth_type_none_for_any_toolset(create: Any) -> None:
    # NONE is not among Jira's supported auth types and is accepted all the same.
    resp = create(instance_body(authType="NONE"))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(instance) == STORED_FIELDS, sorted(instance)
    assert instance["authType"] == "NONE"


def test_create_with_inline_credentials_returns_them_masked(create: Any) -> None:
    auth_config = {"apiToken": "spec-audit-inline-token", "extra": {"nested": True}}

    resp = create(instance_body(authConfig=auth_config, baseUrl="https://spec-audit.invalid"))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    # Stored on the instance as is; only nested objects and client secrets are masked.
    assert instance["auth"] == {"apiToken": auth_config["apiToken"], "extra": MASK}
    assert "oauthConfigId" not in instance


def test_create_oauth_instance_with_client_credentials_creates_an_oauth_config(
    toolsets_client: ToolsetsClient, create: Any
) -> None:
    oauth_name = f"spec-audit-oauth-{uuid.uuid4().hex[:8]}"

    resp = create(oauth_instance_body(oauthInstanceName=oauth_name, baseUrl="https://spec-audit.invalid/"))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert set(instance) == STORED_FIELDS | {"oauthConfigId"}, sorted(instance)

    configs = toolsets_client.list_oauth_configs(TOOLSET_TYPE)
    assert configs.status_code == 200, configs.text[:500]
    config = next(c for c in configs.json()["oauthConfigs"] if c["_id"] == instance["oauthConfigId"])
    assert config["oauthInstanceName"] == oauth_name
    assert config["clientId"] == OAUTH_CLIENT_AUTH["clientId"]
    # baseUrl is only used to build the redirect URI of the new OAuth config.
    assert config["redirectUri"].startswith("https://spec-audit.invalid/toolsets/oauth/callback")


def test_create_oauth_instance_linked_to_an_existing_oauth_config(
    create: Any, seed_toolset_instance: SeedToolsetInstance
) -> None:
    existing = seed_toolset_instance(oauth=True)

    resp = create(instance_body(authType="OAUTH", oauthConfigId=existing["oauthConfigId"]))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert instance["oauthConfigId"] == existing["oauthConfigId"]
    assert "auth" not in instance


def test_create_oauth_instance_without_client_credentials_has_no_oauth_config(create: Any) -> None:
    resp = create(instance_body(authType="OAUTH"))
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert "oauthConfigId" not in instance
    # An empty placeholder is stored inline; the mask stands for the empty secret too.
    assert instance["auth"] == {"type": "OAUTH", "clientId": "", "clientSecret": MASK}


def test_create_drops_fields_the_validator_does_not_list(create: Any) -> None:
    body = {**instance_body(authType="OAUTH"), **OAUTH_CLIENT_AUTH, "specAuditUnknown": 1}

    with outside_request_contract("the validator drops unknown fields, top-level client credentials included"):
        resp = create(body)
    instance = _created(resp)
    assert_strict_openapi_response(resp, ROUTE)
    # Python would read a top-level clientId, but Node never forwards it.
    assert instance["auth"] == {"type": "OAUTH", "clientId": "", "clientSecret": MASK}


@pytest.mark.parametrize(
    ("change", "fields"),
    [
        pytest.param(None, {"body.instanceName", "body.toolsetType", "body.authType"}, id="no-body"),
        pytest.param({"instanceName": None}, {"body.instanceName"}, id="instanceName-missing"),
        pytest.param({"instanceName": ""}, {"body.instanceName"}, id="instanceName-empty"),
        # The global sanitizer trims every string before the validator sees it.
        pytest.param({"instanceName": "   "}, {"body.instanceName"}, id="instanceName-blank"),
        pytest.param({"instanceName": 5}, {"body.instanceName"}, id="instanceName-not-text"),
        pytest.param({"toolsetType": None}, {"body.toolsetType"}, id="toolsetType-missing"),
        pytest.param({"toolsetType": " "}, {"body.toolsetType"}, id="toolsetType-blank"),
        pytest.param({"toolsetType": 5}, {"body.toolsetType"}, id="toolsetType-not-text"),
        pytest.param({"authType": None}, {"body.authType"}, id="authType-missing"),
        pytest.param({"authType": ""}, {"body.authType"}, id="authType-empty"),
        pytest.param({"authType": 5}, {"body.authType"}, id="authType-not-text"),
        pytest.param({"authConfig": "apiToken"}, {"body.authConfig"}, id="authConfig-not-object"),
        pytest.param({"authConfig": ["apiToken"]}, {"body.authConfig"}, id="authConfig-list"),
        pytest.param({"baseUrl": 5}, {"body.baseUrl"}, id="baseUrl-not-text"),
        pytest.param({"oauthConfigId": 5}, {"body.oauthConfigId"}, id="oauthConfigId-not-text"),
        pytest.param({"oauthInstanceName": 5}, {"body.oauthInstanceName"}, id="oauthInstanceName-not-text"),
    ],
)
def test_create_rejects_a_body_that_fails_validation(
    toolsets_client: ToolsetsClient, change: JsonObject | None, fields: set[str]
) -> None:
    body = None
    if change is not None:
        body = {key: value for key, value in {**instance_body(), **change}.items() if value is not None}

    resp = toolsets_client.create_instance(body)  # type: ignore[arg-type]
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == fields
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_rejects_a_null_auth_config(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance({**instance_body(), "authConfig": None})
    assert resp.status_code == 400, resp.text[:500]
    assert rejected_fields(resp) == {"body.authConfig"}
    assert_strict_openapi_exchange(resp, ROUTE)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            instance_body(authType="BASIC_AUTH"),
            "Auth type 'BASIC_AUTH' is not supported by toolset 'jira'. Supported: ['OAUTH', 'API_TOKEN']",
            id="auth-type-the-toolset-does-not-support",
        ),
        pytest.param(
            instance_body(authType="OAUTH", authConfig={"clientId": "spec-audit-client-id", "clientSecret": MASK}),
            "Invalid authentication configuration: a masked value cannot be saved. Enter the value again.",
            id="masked-client-secret",
        ),
    ],
)
def test_create_refusals_only_the_backend_makes(
    toolsets_client: ToolsetsClient, body: JsonObject, message: str
) -> None:
    # Both depend on the toolset type or on stored state, which a body schema cannot say.
    resp = toolsets_client.create_instance(body)
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert (error["code"], error["message"]) == ("HTTP_BAD_REQUEST", message)
    assert_strict_openapi_exchange(resp, ROUTE)


def test_create_refuses_a_salesforce_login_url_outside_salesforce(toolsets_client: ToolsetsClient) -> None:
    body = instance_body(
        toolsetType="salesforce",
        authType="OAUTH",
        authConfig={**OAUTH_CLIENT_AUTH, "loginUrl": "http://spec-audit.invalid"},
    )
    resp = toolsets_client.create_instance(body)
    assert resp.status_code == 400, resp.text[:500]
    error = error_of(resp)
    assert error["code"] == "HTTP_BAD_REQUEST"
    assert error["message"].startswith(
        "Invalid authentication configuration: The Salesforce Login URL must be an https address"
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_duplicate_instance_name_is_a_conflict_whatever_its_case(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    existing = seed_toolset_instance()
    with toolset_store_lock():
        resp = toolsets_client.create_instance(instance_body(instanceName=existing["instanceName"].upper()))
    assert resp.status_code == 409, resp.text[:500]
    assert error_of(resp)["message"] == (
        f"A toolset instance named '{existing['instanceName'].upper()}' already exists "
        f"for toolset type '{TOOLSET_TYPE}' in this organization."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_an_instance_name_may_be_reused_for_another_toolset_type(
    create: Any, seed_toolset_instance: SeedToolsetInstance
) -> None:
    existing = seed_toolset_instance()

    resp = create(
        instance_body(
            instanceName=existing["instanceName"], toolsetType=OAUTH_ONLY_LOCAL_TOOLSET_TYPE, authType="NONE"
        )
    )
    instance = _created(resp)
    assert_strict_openapi_exchange(resp, ROUTE)
    assert (instance["instanceName"], instance["toolsetType"]) == (
        existing["instanceName"],
        OAUTH_ONLY_LOCAL_TOOLSET_TYPE,
    )


def test_duplicate_oauth_config_name_is_a_conflict(
    toolsets_client: ToolsetsClient, seed_toolset_instance: SeedToolsetInstance
) -> None:
    # Without oauthInstanceName the new OAuth config is named after the instance.
    existing = seed_toolset_instance(oauth=True)
    with toolset_store_lock():
        resp = toolsets_client.create_instance(oauth_instance_body(oauthInstanceName=existing["instanceName"]))
    assert resp.status_code == 409, resp.text[:500]
    assert error_of(resp)["message"] == (
        f"An OAuth configuration named '{existing['instanceName']}' already exists for toolset '{TOOLSET_TYPE}'."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_toolset_type_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance(instance_body(toolsetType=MISSING_TOOLSET_TYPE))
    assert_not_found(resp, f"Toolset '{MISSING_TOOLSET_TYPE}' not found in registry")
    assert_strict_openapi_exchange(resp, ROUTE)


def test_unknown_oauth_config_id_is_not_found(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance(
        instance_body(authType="OAUTH", oauthConfigId=MISSING_OAUTH_CONFIG_ID)
    )
    assert_not_found(
        resp, "This sign-in app was removed, or you no longer have access. Refresh the page and try again."
    )
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "POST", "/instances", json=instance_body())
    assert resp.status_code == 403, resp.text[:500]
    assert error_of(resp)["message"] == "Only administrators can create toolset instances."
    assert_strict_openapi_exchange(resp, ROUTE)


def test_without_token_is_unauthorized(toolsets_client: ToolsetsClient) -> None:
    resp = toolsets_client.create_instance(instance_body(), auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
