"""Strict OpenAPI audit of PUT /api/v1/configurationManager/ai-models/roles.

Chain: authenticate -> requireScopes(config:write) -> userAdminCheck -> updateModelRoles.
No validator: the handler checks the body itself, and its 400 is a bare
``{status, message}`` object, not the usual error envelope. A success replaces the whole
role map inside the stored AI models configuration; the map in force is put back afterwards.
The one assignment made here points the indexing role at the default embedding model,
which is what indexing uses when no role is assigned.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest
from configuration_manager_audit_support import INVALID_BEARER_HEADERS, request_as
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import (
    assert_spec_forbids_request,
    assert_strict_openapi_exchange,
    assert_strict_openapi_response,
    outside_request_contract,
)

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/roles"
PATH = "/ai-models/roles"
SAVED_MESSAGE = "Model roles updated successfully"
MISSING_MODEL_KEY = "00000000-0000-4000-8000-0000000000aa"


def _roles(config_client: ConfigClient) -> dict[str, Any]:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    roles: dict[str, Any] = resp.json()["modelRoles"]
    return roles


@pytest.fixture
def roles_in_force(config_client: ConfigClient) -> Iterator[dict[str, Any]]:
    before = _roles(config_client)
    try:
        yield dict(before)
    finally:
        if _roles(config_client) != before:
            restored = config_client.put(PATH, json={"roles": before})
            assert restored.status_code == 200, f"model roles not restored: {restored.text[:500]}"


@pytest.fixture
def default_embedding_key(config_client: ConfigClient) -> str:
    resp = config_client.get("/ai-models")
    assert resp.status_code == 200, resp.text[:500]
    defaults = [entry["modelKey"] for entry in resp.json()["models"]["embedding"] if entry.get("isDefault")]
    assert defaults, "the stack has no default embedding model"
    return str(defaults[0])


def test_assigning_a_role_stores_and_echoes_the_map(
    config_client: ConfigClient, roles_in_force: dict[str, Any], default_embedding_key: str
) -> None:
    roles = {**roles_in_force, "indexing": {"modelType": "embedding", "modelKey": default_embedding_key}}

    resp = config_client.put(PATH, json={"roles": roles})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"status": "success", "message": SAVED_MESSAGE, "modelRoles": roles}
    assert _roles(config_client) == roles


def test_saving_the_map_in_force_changes_nothing(config_client: ConfigClient, roles_in_force: dict[str, Any]) -> None:
    resp = config_client.put(PATH, json={"roles": roles_in_force})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _roles(config_client) == roles_in_force


def test_body_fields_other_than_roles_are_ignored(config_client: ConfigClient, roles_in_force: dict[str, Any]) -> None:
    with outside_request_contract("specAudit is not a field of the body"):
        resp = config_client.put(PATH, json={"roles": roles_in_force, "specAudit": "x"})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert "specAudit" not in resp.json()
    assert _roles(config_client) == roles_in_force


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param({}, 'Request body must contain a "roles" object', id="empty-object"),
        pytest.param({"roles": []}, 'Request body must contain a "roles" object', id="roles-a-list"),
        pytest.param({"roles": "indexing"}, 'Request body must contain a "roles" object', id="roles-a-string"),
        pytest.param({"roles": {"indexing": "embedding"}}, 'Role "indexing" assignment must be an object', id="assignment-a-string"),
        pytest.param(
            {"roles": {"indexing": {"modelType": "embedding"}}},
            'Role "indexing" must have both modelType and modelKey',
            id="missing-model-key",
        ),
        pytest.param(
            {"roles": {"indexing": {"modelType": "", "modelKey": MISSING_MODEL_KEY}}},
            'Role "indexing" must have both modelType and modelKey',
            id="empty-model-type",
        ),
        pytest.param(
            {"roles": {"indexing": {"modelType": "vision", "modelKey": MISSING_MODEL_KEY}}},
            'Role "indexing": modelType "vision" is not valid',
            id="unknown-model-type",
        ),
    ],
)
def test_invalid_body_is_refused_by_the_handler(
    config_client: ConfigClient, body: dict[str, Any], message: str
) -> None:
    before = _roles(config_client)

    resp = config_client.put(PATH, json=body)

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {"status": "error", "message": message}
    assert_strict_openapi_exchange(resp, ROUTE)
    assert_spec_forbids_request(resp, ROUTE)
    assert _roles(config_client) == before


def test_a_model_key_that_is_not_configured_is_refused(config_client: ConfigClient) -> None:
    before = _roles(config_client)

    resp = config_client.put(PATH, json={"roles": {"indexing": {"modelType": "embedding", "modelKey": MISSING_MODEL_KEY}}})

    assert resp.status_code == 400, resp.text[:500]
    assert resp.json() == {
        "status": "error",
        "message": f'Role "indexing": no model with key "{MISSING_MODEL_KEY}" found in "embedding"',
    }
    assert_strict_openapi_exchange(resp, ROUTE)
    assert _roles(config_client) == before


@pytest.mark.parametrize("headers", [None, INVALID_BEARER_HEADERS], ids=["no-token", "invalid-token"])
def test_without_valid_token_is_unauthorized(config_client: ConfigClient, headers: dict[str, str] | None) -> None:
    resp = config_client.put(PATH, auth=False, headers=headers, json={"roles": {}})
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_member_is_forbidden(config_client: ConfigClient, second_user: SecondUser) -> None:
    before = _roles(config_client)

    resp = request_as(second_user, "PUT", PATH, json={"roles": {}})

    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert _roles(config_client) == before


def test_token_without_config_write_scope_is_forbidden(config_client: ConfigClient, narrow_scope_headers: dict[str, str]) -> None:
    resp = config_client.put("/ai-models/roles", auth=False, headers=narrow_scope_headers, json={"roles": {}})

    assert resp.status_code == 403, resp.text[:500]
    assert resp.json()["error"]["code"] == "HTTP_FORBIDDEN", resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)


def test_extra_keys_inside_an_assignment_are_stored_and_echoed(
    config_client: ConfigClient, roles_in_force: dict[str, Any], default_embedding_key: str
) -> None:
    # API bug: the handler checks modelType and modelKey but keeps whatever else the assignment holds.
    assignment = {"modelType": "embedding", "modelKey": default_embedding_key, "specAudit": "x"}

    with outside_request_contract("specAudit is not a field of a role assignment"):
        resp = config_client.put(PATH, json={"roles": {**roles_in_force, "indexing": assignment}})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["modelRoles"]["indexing"] == assignment
    assert _roles(config_client)["indexing"] == assignment
    stored = config_client.get("/aiModelsConfig")
    assert stored.status_code == 200, stored.text[:500]
    assert_strict_openapi_exchange(stored, "/api/v1/configurationManager/aiModelsConfig")
    assert stored.json()["modelRoles"]["indexing"] == assignment
