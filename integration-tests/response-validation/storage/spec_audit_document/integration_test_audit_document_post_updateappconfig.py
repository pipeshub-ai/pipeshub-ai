"""Strict OpenAPI audit of POST /api/v1/document/updateAppConfig."""

from __future__ import annotations

import pytest
import requests
from document_audit_support import (
    FETCH_CONFIG_SCOPE,
    UPDATE_APP_CONFIG_ROUTE,
    DocumentClient,
    mint_scoped_token,
)
from helper.pipeshub_client import PipeshubClient
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/document/updateAppConfig"
assert ROUTE == UPDATE_APP_CONFIG_ROUTE


def _assert_unauthorized(resp: requests.Response, message: str) -> None:
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json()["error"]["message"] == message, resp.text[:500]


def test_update_app_config_reloads_storage_config_with_fetch_config_token(
    document_client: DocumentClient, fetch_config_token: str
) -> None:
    # Safe on a shared org: the handler re-reads the stored config and rebinds it unchanged.
    resp = document_client.update_app_config(token=fetch_config_token)
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"message": "Storage configuration updated successfully"}


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
