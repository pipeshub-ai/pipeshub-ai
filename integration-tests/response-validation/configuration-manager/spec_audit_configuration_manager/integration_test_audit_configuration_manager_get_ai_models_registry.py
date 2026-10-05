"""Strict OpenAPI audit of GET /api/v1/configurationManager/ai-models/registry."""

from __future__ import annotations

import pytest
from configuration_manager_audit_support import (
    assert_strict_openapi_response_keeping_field_names,
    request_as,
)
from helper.clients.config_client import ConfigClient
from helper.second_user import SecondUser
from strict_openapi import assert_strict_openapi_response

pytestmark = pytest.mark.spec_audit

ROUTE = "/api/v1/configurationManager/ai-models/registry"
PATH = "/ai-models/registry"


def test_admin_lists_every_registered_provider(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH)
    assert resp.status_code == 200, resp.text[:500]
    # Some field descriptors carry an "examples" list, which the shared check cannot see in the spec.
    assert_strict_openapi_response_keeping_field_names(resp, ROUTE)
    body = resp.json()
    assert body["success"] is True
    # The registry is filled at import time on the Python side, so it is never empty.
    assert body["providers"], "the provider registry came back empty"
    assert body["total"] == len(body["providers"])
    assert all(provider["providerId"] for provider in body["providers"])


def test_capability_filter_keeps_only_matching_providers(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, params={"capability": "embedding"})
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response_keeping_field_names(resp, ROUTE)
    body = resp.json()
    assert body["providers"], "no provider advertises the embedding capability"
    assert body["total"] == len(body["providers"])
    assert all("embedding" in provider["capabilities"] for provider in body["providers"])


def test_unknown_capability_is_an_empty_list_not_a_400(config_client: ConfigClient) -> None:
    # Neither Node nor Python validates the query: a value outside the capability
    # list is a plain substring filter that matches nothing.
    resp = config_client.get(
        PATH, params={"search": "a", "capability": "spec-audit-no-such-capability"}
    )
    assert resp.status_code == 200, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
    assert resp.json() == {"success": True, "providers": [], "total": 0}


def test_no_token_is_rejected(config_client: ConfigClient) -> None:
    resp = config_client.get(PATH, auth=False)
    assert resp.status_code == 401, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)


def test_member_is_forbidden(second_user: SecondUser) -> None:
    resp = request_as(second_user, "GET", PATH)
    assert resp.status_code == 403, resp.text[:500]
    assert_strict_openapi_response(resp, ROUTE)
