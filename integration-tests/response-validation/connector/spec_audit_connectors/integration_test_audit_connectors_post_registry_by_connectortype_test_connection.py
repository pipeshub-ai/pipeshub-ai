"""Strict OpenAPI audit of POST /api/v1/connectors/registry/:connectorType/test-connection."""

from __future__ import annotations

import os
import socket
from typing import Any, Iterator
from urllib.parse import quote

import pytest
from connectors_audit_support import (
    MISSING_CONNECTOR_ID,
    MISSING_CONNECTOR_TYPE,
    SEED_CONNECTOR_TYPE,
    UNSAFE_CONNECTOR_ID,
    ConnectorsAuditClient,
    bearer,
    created_connector_id,
    request_as,
    unique_name,
    wait_until_deleted,
)
from helper.connector_lifecycle import source_unavailable
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/connectors/registry/:connectorType/test-connection"

# The only registered type whose connector implements a connection check.
CHECKED_TYPE = "PostgreSQL"
# Nothing listens on port 1, so the check fails without leaving the connector service's host.
UNREACHABLE_AUTH = {
    "host": "127.0.0.1",
    "port": "1",
    "database": "spec_audit",
    "username": "spec_audit",
    "password": "spec-audit",
}
REFUSED = "127.0.0.1:1 refused the connection."
MISSING_SETTINGS = "Host, database and username are required."


def _path(connector_type: str = CHECKED_TYPE) -> str:
    return f"/registry/{quote(connector_type, safe='%')}/test-connection"


def _error(resp: Any, status: int, code: str) -> str:
    assert resp.status_code == status, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    error = resp.json()["error"]
    assert error["code"] == code, resp.text[:500]
    return str(error["message"])


@pytest.fixture(scope="module")
def postgres_auth() -> dict[str, Any]:
    """Auth settings of the stack's PostgreSQL source, as the connector service reaches it."""
    host = os.getenv("POSTGRES_TEST_HOST", "localhost")
    port = int(os.getenv("POSTGRES_TEST_PORT", "5433"))
    try:
        socket.create_connection((host, port), timeout=5).close()
    except OSError as exc:
        source_unavailable(f"PostgreSQL source not reachable at {host}:{port}: {exc}")
    return {
        "host": os.getenv("POSTGRES_CONNECTOR_HOST", "postgres-source"),
        "port": int(os.getenv("POSTGRES_CONNECTOR_PORT", "5432")),
        "database": os.getenv("POSTGRES_TEST_DB", "pipeshub_connector_test"),
        "username": os.getenv("POSTGRES_TEST_USER", "pipeshubtest"),
        "password": os.getenv("POSTGRES_TEST_PASSWORD", "pipeshubtest123"),
    }


@pytest.fixture
def postgres_instance(connectors_client: ConnectorsAuditClient) -> Iterator[str]:
    """An admin's PostgreSQL instance whose saved auth settings point at nothing."""
    connector_id = created_connector_id(
        connectors_client.create_instance(
            connectorType=CHECKED_TYPE,
            instanceName=unique_name(),
            scope="team",
            authType="BASIC_AUTH",
            config={"auth": UNREACHABLE_AUTH},
        )
    )
    try:
        yield connector_id
    finally:
        deleted = connectors_client.delete_instance(connector_id)
        assert deleted.status_code == 202, deleted.text[:300]
        wait_until_deleted(connectors_client, connector_id)


def test_a_source_that_cannot_be_reached_is_a_failed_check_not_an_error(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(), json={"auth": UNREACHABLE_AUTH})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False
    assert body["message"].startswith(REFUSED), body


def test_settings_that_leave_out_the_host_are_a_failed_check(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(), json={"auth": {}})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": False, "message": MISSING_SETTINGS}


def test_working_settings_are_a_passed_check(
    connectors_client: ConnectorsAuditClient, postgres_auth: dict[str, Any]
) -> None:
    resp = connectors_client.post(_path(), json={"auth": postgres_auth})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {
        "success": True,
        "message": f'Connected to database "{postgres_auth["database"]}" as "{postgres_auth["username"]}".',
    }


def test_a_login_the_database_refuses_is_a_failed_check(
    connectors_client: ConnectorsAuditClient, postgres_auth: dict[str, Any]
) -> None:
    # An unknown user, not a wrong password: a database that trusts local connections accepts any password.
    resp = connectors_client.post(_path(), json={"auth": {**postgres_auth, "username": "spec_audit_no_such_user"}})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["success"] is False


def test_a_non_admin_member_may_run_a_check(second_user: SecondUser) -> None:
    # No userAdminCheck on this route, in Node or in Python.
    resp = request_as(second_user, "POST", _path(), json={"auth": UNREACHABLE_AUTH})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["success"] is False


def test_unlisted_body_fields_are_dropped(connectors_client: ConnectorsAuditClient) -> None:
    with outside_request_contract("the validator strips the body fields it does not list"):
        resp = connectors_client.post(_path(), json={"auth": {}, "instanceName": "ignored"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["message"] == MISSING_SETTINGS


def test_query_parameters_are_ignored(connectors_client: ConnectorsAuditClient) -> None:
    with outside_request_contract("the route validates only the path parameter and the body"):
        resp = connectors_client.post(_path(), json={"auth": {}}, params={"scope": "nonsense"})
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_the_instance_s_saved_settings_fill_in_what_is_left_out(
    connectors_client: ConnectorsAuditClient, postgres_instance: str
) -> None:
    resp = connectors_client.post(_path(), json={"auth": {}, "connectorId": postgres_instance})

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    body = resp.json()
    assert body["success"] is False
    assert body["message"].startswith(REFUSED), body


def test_a_sent_setting_replaces_the_saved_one(
    connectors_client: ConnectorsAuditClient, postgres_instance: str
) -> None:
    resp = connectors_client.post(
        _path(), json={"auth": {"host": ""}, "connectorId": postgres_instance}
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"success": False, "message": MISSING_SETTINGS}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(None, "Auth is required.", id="no-body"),
        pytest.param({}, "Auth is required.", id="auth-missing"),
        pytest.param({"auth": None}, "Auth is required.", id="auth-null"),
        pytest.param({"auth": "host=db"}, "Auth must be a group of fields.", id="auth-string"),
        pytest.param({"auth": [1]}, "Auth must be a group of fields.", id="auth-array"),
        pytest.param([1], "The request must be a group of fields.", id="body-array"),
        pytest.param(
            {"auth": {}, "connectorId": "bad.id"},
            "Connector ID must be 1-64 chars of letters, digits, underscore, or hyphen",
            id="connector-id-bad-characters",
        ),
        pytest.param(
            {"auth": {}, "connectorId": ""},
            "Connector ID must be 1-64 chars of letters, digits, underscore, or hyphen",
            id="connector-id-empty",
        ),
        pytest.param({"auth": {}, "connectorId": 5}, "Connector id must be text.", id="connector-id-number"),
    ],
)
def test_an_invalid_body_is_a_validation_error(
    connectors_client: ConnectorsAuditClient, body: Any, message: str
) -> None:
    resp = connectors_client.post(_path()) if body is None else connectors_client.post(_path(), json=body)

    assert _error(resp, 400, "VALIDATION_ERROR") == message


def test_a_type_without_a_connection_check_is_a_bad_request(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(SEED_CONNECTOR_TYPE), json={"auth": {}})

    assert _error(resp, 400, "HTTP_BAD_REQUEST") == f"{SEED_CONNECTOR_TYPE} has no connection check"


def test_an_instance_of_another_type_is_a_bad_request(
    connectors_client: ConnectorsAuditClient, connector_id: str
) -> None:
    resp = connectors_client.post(_path(), json={"auth": {}, "connectorId": connector_id})

    assert _error(resp, 400, "HTTP_BAD_REQUEST") == "connectorId is not a connector of this type"


def test_an_unsafe_type_is_a_bad_request_before_authentication(
    connectors_client: ConnectorsAuditClient,
) -> None:
    # guardPathParams is a router.param hook, so it answers ahead of authenticate.
    resp = connectors_client.post(_path(UNSAFE_CONNECTOR_ID), json={"auth": {}}, auth=False)

    _error(resp, 400, "HTTP_BAD_REQUEST")


@pytest.mark.parametrize(
    "connector_type",
    [
        pytest.param(MISSING_CONNECTOR_TYPE, id="unregistered"),
        pytest.param(CHECKED_TYPE.lower(), id="wrong-case"),
    ],
)
def test_an_unknown_type_is_not_found(
    connectors_client: ConnectorsAuditClient, connector_type: str
) -> None:
    resp = connectors_client.post(_path(connector_type), json={"auth": {}})

    assert _error(resp, 404, "HTTP_NOT_FOUND") == f"Connector type '{connector_type}' not found in registry"


def test_an_instance_that_does_not_exist_is_not_found(
    connectors_client: ConnectorsAuditClient,
) -> None:
    resp = connectors_client.post(_path(), json={"auth": {}, "connectorId": MISSING_CONNECTOR_ID})

    _error(resp, 404, "HTTP_NOT_FOUND")


def test_another_user_s_instance_is_not_found_for_a_member(
    second_user: SecondUser, postgres_instance: str
) -> None:
    resp = request_as(second_user, "POST", _path(), json={"auth": {}, "connectorId": postgres_instance})

    _error(resp, 404, "HTTP_NOT_FOUND")


def test_no_token_is_unauthorized(connectors_client: ConnectorsAuditClient) -> None:
    resp = connectors_client.post(_path(), json={"auth": {}}, auth=False)

    _error(resp, 401, "HTTP_UNAUTHORIZED")


@pytest.mark.parametrize("scopes", [(), ("connector:read",)], ids=["no-connector-scope", "read-only"])
def test_a_token_without_connector_write_is_forbidden(
    connectors_client: ConnectorsAuditClient,
    token_without_connector_scopes: str,
    token_with_scopes: Any,
    scopes: tuple[str, ...],
) -> None:
    token = token_with_scopes(*scopes) if scopes else token_without_connector_scopes
    resp = connectors_client.post(_path(), json={"auth": {}}, auth=False, headers=bearer(token))

    assert _error(resp, 403, "HTTP_FORBIDDEN") == "Insufficient scope. Required: connector:write"


def test_a_token_with_connector_write_may_run_a_check(
    connectors_client: ConnectorsAuditClient, token_with_scopes: Any
) -> None:
    resp = connectors_client.post(
        _path(), json={"auth": {}}, auth=False, headers=bearer(token_with_scopes("connector:write"))
    )

    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
