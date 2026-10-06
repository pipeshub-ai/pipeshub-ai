"""Strict OpenAPI audit of POST /api/v1/document/updateAppConfig.

scopedTokenValidator(fetch:config) -> handler that re-reads the stored configuration and
rebinds the storage config and controller. No validator, and the handler never looks at
the request: there is nothing to send.
"""

from __future__ import annotations

import pytest
import requests
from document_audit_support import (
    FETCH_CONFIG_SCOPE,
    MALFORMED_TOKEN,
    UPDATE_APP_CONFIG_ROUTE,
    DocumentClient,
    mint_scoped_token,
    request_as,
)
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_exchange, outside_request_contract

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/document/updateAppConfig"
assert ROUTE == UPDATE_APP_CONFIG_ROUTE


def _assert_unauthorized(resp: requests.Response, message: str) -> None:
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json()["error"]["message"] == message, resp.text[:500]


def test_update_app_config_reloads_storage_config_with_fetch_config_token(
    document_client: DocumentClient, fetch_config_token: str
) -> None:
    # Safe on a shared org: the handler re-reads the stored config and rebinds it unchanged.
    resp = document_client.update_app_config(token=fetch_config_token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Storage configuration updated successfully"}


def test_update_app_config_is_repeatable(
    document_client: DocumentClient, fetch_config_token: str
) -> None:
    for _ in range(2):
        resp = document_client.update_app_config(token=fetch_config_token)
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)


def test_update_app_config_does_not_read_the_request(
    document_client: DocumentClient, fetch_config_token: str
) -> None:
    # A storage config for a backend that does not exist: were it read, the next call would fail.
    with outside_request_contract("the handler never reads the body or the query string"):
        resp = document_client.update_app_config(
            token=fetch_config_token,
            params={"storageType": "s3"},
            json={"storageType": "s3", "endpoint": "http://127.0.0.1:9", "bucket": "spec-audit-none"},
        )
        assert resp.status_code == 200, resp.text[:500]
        assert_strict_openapi_exchange(resp, ROUTE)
    assert resp.json() == {"message": "Storage configuration updated successfully"}

    again = document_client.update_app_config(token=fetch_config_token)
    assert again.status_code == 200, again.text[:500]
    assert_strict_openapi_exchange(again, ROUTE)


def test_update_app_config_refuses_a_malformed_token(document_client: DocumentClient) -> None:
    _assert_unauthorized(document_client.update_app_config(token=MALFORMED_TOKEN), "Invalid token")


def test_update_app_config_refuses_a_member_session_token(second_user: SecondUser) -> None:
    _assert_unauthorized(request_as(second_user, "POST", "/updateAppConfig"), "Invalid token")


def test_update_app_config_without_token_is_unauthorized(
    document_client: DocumentClient,
) -> None:
    _assert_unauthorized(document_client.update_app_config(auth=False), "No token provided")


def test_update_app_config_refuses_admin_session_token(
    document_client: DocumentClient,
) -> None:
    # No admin gate here: a user JWT fails signature verification, so 401 and not 403.
    _assert_unauthorized(document_client.update_app_config(), "Invalid token")


def test_update_app_config_refuses_token_signed_with_another_key(
    document_client: DocumentClient, pipeshub_client: PipeshubClient
) -> None:
    token = mint_scoped_token(
        pipeshub_client.org_id, [FETCH_CONFIG_SCOPE], secret="spec-audit-wrong-secret"
    )
    _assert_unauthorized(document_client.update_app_config(token=token), "Invalid token")


def test_update_app_config_refuses_valid_token_with_other_scope(
    document_client: DocumentClient, storage_scope_token: str
) -> None:
    _assert_unauthorized(
        document_client.update_app_config(token=storage_scope_token), "Invalid scope"
    )
