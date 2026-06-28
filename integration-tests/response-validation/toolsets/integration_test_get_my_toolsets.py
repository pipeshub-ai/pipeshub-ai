"""
Toolsets API – Response Validation Integration Tests
====================================================

Covers the single route::

    GET /api/v1/toolsets/my-toolsets   — operationId ``getMyToolsets``

(``Toolset Instances`` tag in ``pipeshub-openapi.yaml``). Every response body is
validated against the ``application/json`` schema declared in the spec via
:func:`openapi_schema_validator.assert_response_matches_openapi_operation`, so the
test fails if the live response drifts from the documented contract (the 200
``toolsets[]`` item uses ``additionalProperties: false``, so an undocumented field
is a hard failure).

Request variations mirror the Zod validator ``getMyToolsetsSchema`` in
``toolsets_routes.ts`` — ``page``, ``limit``, ``search``, ``includeRegistry``,
``toolsetType``, ``authStatus`` — plus the validation failures that schema rejects.

Auth:
  Uses the session ``pipeshub_client`` fixture from the root ``conftest.py``.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from openapi_schema_validator import (  # noqa: E402
    assert_response_matches_openapi_operation,
)

PATH = "/api/v1/toolsets/my-toolsets"
OP = "getMyToolsets"


def _get(
    client: PipeshubClient,
    *,
    auth: bool = True,
    **params: Any,
) -> requests.Response:
    """GET /my-toolsets with the given query params (None values dropped)."""
    query = {k: v for k, v in params.items() if v is not None}
    return client.request("GET", PATH, params=query or None, auth=auth)


def _assert_error_envelope(body: object) -> None:
    """Spec error bodies are ``{ error: { code, message } }``."""
    assert isinstance(body, dict), f"Expected error object, got {type(body).__name__}"
    err = body.get("error", {})
    assert isinstance(err, dict) and err.get("message"), f"Expected error envelope: {body}"


def _valid_toolset() -> dict[str, Any]:
    """A spec-conformant ``toolsets[]`` item (modelled on a real Confluence entry)."""
    return {
        "instanceId": "4c6ec361-ceb9-440a-a633-defa4c77858d",
        "instanceName": "Confluence",
        "toolsetType": "confluence",
        "authType": "OAUTH",
        "oauthConfigId": "ef57d217-f5a4-4782-86c4-d0666d4ec6a5",
        "displayName": "Confluence",
        "description": "Confluence integration",
        "iconPath": "/icons/connectors/confluence.svg",
        "category": "app",
        "supportedAuthTypes": ["OAUTH", "API_TOKEN"],
        "documentationLinks": [
            {"title": "Setup", "url": "https://example.com", "type": "setup"}
        ],
        "toolCount": 1,
        "tools": [
            {"name": "create_page", "fullName": "confluence.create_page", "description": "d"}
        ],
        "isConfigured": True,
        "isAuthenticated": False,
        "isFromRegistry": False,
        "createdBy": "6a32b0f6ab2c443828b6419f",
        "createdAtTimestamp": 1781707292013,
        "updatedAtTimestamp": 1781707292013,
        "auth": None,
    }


def _valid_body(toolset: dict[str, Any] | None = None) -> dict[str, Any]:
    """A spec-conformant ``getMyToolsets`` 200 envelope wrapping one toolset."""
    return {
        "status": "success",
        "pagination": {
            "page": 1, "limit": 20, "total": 1,
            "totalPages": 1, "hasNext": False, "hasPrev": False,
        },
        "filterCounts": {"all": 1, "authenticated": 0, "notAuthenticated": 1},
        "toolsets": [toolset or _valid_toolset()],
    }


# ==================================================================== #
# 200 — valid request variations from getMyToolsetsSchema
# ==================================================================== #
@pytest.mark.integration
class TestGetMyToolsetsSuccess:
    """GET /api/v1/toolsets/my-toolsets — documented 200 contract."""

    def test_defaults(self, pipeshub_client: PipeshubClient) -> None:
        """No params: defaults applied, full envelope matches the spec."""
        resp = _get(pipeshub_client)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, OP)

        assert body["status"] == "success"
        assert body["pagination"]["page"] == 1, "No page param should return the first page"
        assert isinstance(body["pagination"]["limit"], int)
        assert isinstance(body["toolsets"], list)
        for key in ("all", "authenticated", "notAuthenticated"):
            assert key in body["filterCounts"], f"filterCounts missing {key!r}"

    def test_explicit_pagination(self, pipeshub_client: PipeshubClient) -> None:
        """page/limit echoed back; page size honoured."""
        resp = _get(pipeshub_client, page="2", limit="5")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, OP)
        assert body["pagination"]["page"] == 2
        assert body["pagination"]["limit"] == 5
        assert len(body["toolsets"]) <= 5

    def test_search_no_match_is_empty(self, pipeshub_client: PipeshubClient) -> None:
        """A search term that matches nothing returns an empty list, still 200."""
        resp = _get(pipeshub_client, search=f"zz-no-such-toolset-{uuid.uuid4().hex}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, OP)
        assert body["toolsets"] == []

    def test_include_registry(self, pipeshub_client: PipeshubClient) -> None:
        """includeRegistry=true adds synthetic registry entries (never fewer than false)."""
        with_registry = _get(pipeshub_client, includeRegistry="true", limit="200")
        without_registry = _get(pipeshub_client, includeRegistry="false", limit="200")
        for resp in (with_registry, without_registry):
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert_response_matches_openapi_operation(resp.json(), OP)

        included = with_registry.json()["toolsets"]
        assert len(included) >= len(without_registry.json()["toolsets"])
        # Registry-only entries are synthetic: no instanceId, flagged isFromRegistry.
        for ts in included:
            if ts["isFromRegistry"]:
                assert ts["instanceId"] == "", "Registry entry must have an empty instanceId"
                assert ts["isConfigured"] is False

    def test_toolset_type_filter(self, pipeshub_client: PipeshubClient) -> None:
        """toolsetType filters to that type (case-insensitive per the spec)."""
        resp = _get(pipeshub_client, toolsetType="jira", includeRegistry="true")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, OP)
        for ts in body["toolsets"]:
            assert ts["toolsetType"].lower() == "jira", f"Unexpected type: {ts['toolsetType']!r}"

    @pytest.mark.parametrize(
        ("auth_status", "expected_authenticated"),
        [("authenticated", True), ("not-authenticated", False)],
    )
    def test_auth_status_filter(
        self,
        pipeshub_client: PipeshubClient,
        auth_status: str,
        expected_authenticated: bool,
    ) -> None:
        """authStatus filters the list to that auth state for the caller."""
        resp = _get(pipeshub_client, authStatus=auth_status, includeRegistry="true", limit="200")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert_response_matches_openapi_operation(body, OP)
        for ts in body["toolsets"]:
            assert ts["isAuthenticated"] is expected_authenticated, (
                f"authStatus={auth_status!r} returned isAuthenticated={ts['isAuthenticated']}"
            )

    def test_all_params_combined(self, pipeshub_client: PipeshubClient) -> None:
        """Every supported param together still yields a spec-valid 200."""
        resp = _get(
            pipeshub_client,
            page="1",
            limit="10",
            search="a",
            includeRegistry="true",
            toolsetType="confluence",
            authStatus="not-authenticated",
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert_response_matches_openapi_operation(resp.json(), OP)


# ==================================================================== #
# 401 / 400 — documented error responses
# ==================================================================== #
@pytest.mark.integration
class TestGetMyToolsetsErrors:
    """Auth and query-validation failures match the documented error responses."""

    def test_unauthenticated(self, pipeshub_client: PipeshubClient) -> None:
        """Missing bearer token → 401 with the documented error envelope."""
        resp = _get(pipeshub_client, auth=False)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        body = resp.json()
        _assert_error_envelope(body)
        assert_response_matches_openapi_operation(body, OP, status_code="401")

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"page": "0"}, id="page-below-min"),
            pytest.param({"page": "-1"}, id="page-negative"),
            pytest.param({"page": "abc"}, id="page-non-numeric"),
            pytest.param({"limit": "0"}, id="limit-below-min"),
            pytest.param({"limit": "201"}, id="limit-above-max"),
            pytest.param({"authStatus": "bogus"}, id="authStatus-bad-enum"),
        ],
    )
    def test_invalid_query_params(
        self, pipeshub_client: PipeshubClient, params: dict[str, str]
    ) -> None:
        """Values the Zod schema rejects → 400 with the documented error envelope."""
        resp = _get(pipeshub_client, **params)
        assert resp.status_code == 400, f"Expected 400 for {params}, got {resp.status_code}: {resp.text}"
        body = resp.json()
        _assert_error_envelope(body)
        assert_response_matches_openapi_operation(body, OP, status_code="400")


# ==================================================================== #
# Schema contract — offline positive/negative validation (no server)
# ==================================================================== #
@pytest.mark.integration
class TestGetMyToolsetsSchemaContract:
    """Prove the spec's 200 schema accepts a conformant body and rejects violations.

    These guard the live ``assert_response_matches_openapi_operation`` checks above:
    if the validator passed everything, the positive assertions would be worthless.
    The ``toolsets[]`` item is ``additionalProperties: false`` with typed/enum fields,
    so undocumented keys, wrong types, and bad enums must all fail.
    """

    def test_conformant_body_validates(self) -> None:
        """A hand-built spec-conformant 200 body passes validation (positive)."""
        assert_response_matches_openapi_operation(_valid_body(), OP)

    def test_nullable_auth_accepts_both_null_and_object(self) -> None:
        """``auth`` is a nullable object: null and a populated object both validate."""
        assert_response_matches_openapi_operation(_valid_body(), OP)  # auth=None
        with_obj = _valid_toolset()
        with_obj["auth"] = {"accessToken": "x"}
        assert_response_matches_openapi_operation(_valid_body(with_obj), OP)

    def _mutate(self, **changes: Any) -> dict[str, Any]:
        ts = _valid_toolset()
        ts.update(changes)
        return _valid_body(ts)

    def test_extra_undocumented_toolset_field_rejected(self) -> None:
        """additionalProperties:false — an unknown key fails (catches response drift)."""
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                self._mutate(surpriseField="x"), OP
            )

    @pytest.mark.parametrize("bad_auth_type", ["MAGIC", "oauth", ""])
    def test_authtype_outside_enum_rejected(self, bad_auth_type: str) -> None:
        """authType must be one of the documented enum values."""
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                self._mutate(authType=bad_auth_type), OP
            )

    def test_category_outside_enum_rejected(self) -> None:
        """category is constrained to [app, database]."""
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                self._mutate(category="weird"), OP
            )

    def test_toolcount_wrong_type_rejected(self) -> None:
        """toolCount must be an integer, not a string."""
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                self._mutate(toolCount="one"), OP
            )

    def test_isauthenticated_wrong_type_rejected(self) -> None:
        """isAuthenticated must be a boolean."""
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(
                self._mutate(isAuthenticated="nope"), OP
            )

    def test_extra_field_on_nested_tool_rejected(self) -> None:
        """tools[] items are additionalProperties:false."""
        ts = _valid_toolset()
        ts["tools"][0]["unexpected"] = "y"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(_valid_body(ts), OP)

    def test_extra_field_on_documentation_link_rejected(self) -> None:
        """documentationLinks[] items are additionalProperties:false."""
        ts = _valid_toolset()
        ts["documentationLinks"][0]["unexpected"] = "z"
        with pytest.raises(AssertionError):
            assert_response_matches_openapi_operation(_valid_body(ts), OP)
