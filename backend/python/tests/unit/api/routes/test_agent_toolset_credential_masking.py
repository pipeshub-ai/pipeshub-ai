"""An agent's editor sees whether credentials exist, never the value of a secret."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.toolsets import (
    _build_toolsets_list_response,
    update_agent_toolset_credentials,
)
from app.config.redaction import REDACTED_PLACEHOLDER
from tests.support.bound_org import bind_test_org

INSTANCE = {"_id": "i1", "orgId": "o1", "toolsetType": "jira", "authType": "API_TOKEN", "instanceName": "J"}


@pytest.fixture(autouse=True)
def _org():  # noqa: ANN202
    yield from bind_test_org("o1")


def _request(body=None) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": "o1"}
    req.headers = {"authorization": "Bearer t"}
    req.body = AsyncMock(return_value=json.dumps(body or {}).encode())
    req.app.state.toolset_registry = MagicMock()
    return req


@pytest.mark.asyncio
async def test_list_masks_stored_values_but_keeps_has_credentials() -> None:
    stored = {"isAuthenticated": True, "auth": {"apiToken": "super-secret", "baseUrl": "https://acme.example", "empty": ""}}
    with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[INSTANCE]):
        result = await _build_toolsets_list_response(
            request=_request(), org_id="o1", config_service=AsyncMock(), search=None, page=1, limit=20,
            include_registry=False, fetch_auth_for_instance=AsyncMock(return_value=stored),
            include_has_credentials=True, expose_non_oauth_auth=True, mask_non_oauth_auth=True,
        )
    entry = result["toolsets"][0]
    assert entry["hasCredentials"] is True
    assert entry["isAuthenticated"] is True
    assert entry["auth"] == {"apiToken": REDACTED_PLACEHOLDER, "baseUrl": REDACTED_PLACEHOLDER, "empty": ""}
    assert "super-secret" not in str(entry["auth"])


JIRA_META = {
    "config": {"auth": {"schemas": {"API_TOKEN": {"fields": [
        {"name": "baseUrl", "fieldType": "URL"},
        {"name": "email", "fieldType": "TEXT"},
        {"name": "apiToken", "fieldType": "PASSWORD", "isSecret": True},
        {"name": "secondKey", "fieldType": "TEXT", "is_secret": True},
        {"name": "profile", "fieldType": "TEXT"},
    ]}}}},
}


def _request_with_schema(body=None) -> MagicMock:
    req = _request(body)
    req.app.state.toolset_registry.get_toolset_metadata.return_value = JIRA_META
    return req


@pytest.mark.asyncio
async def test_list_masks_only_declared_secrets_and_undeclared_fields() -> None:
    stored = {"isAuthenticated": True, "auth": {
        "apiToken": "super-secret", "secondKey": "second-secret", "baseUrl": "https://acme.example",
        "email": "a@acme.example", "unknownField": "unknown-value", "profile": {"token": "nested-secret"}, "empty": "",
    }}
    with patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[INSTANCE]):
        result = await _build_toolsets_list_response(
            request=_request_with_schema(), org_id="o1", config_service=AsyncMock(), search=None, page=1, limit=20,
            include_registry=False, fetch_auth_for_instance=AsyncMock(return_value=stored),
            include_has_credentials=True, expose_non_oauth_auth=True, mask_non_oauth_auth=True,
        )
    assert result["toolsets"][0]["auth"] == {
        "apiToken": REDACTED_PLACEHOLDER,
        "secondKey": REDACTED_PLACEHOLDER,
        "baseUrl": "https://acme.example",
        "email": "a@acme.example",
        "unknownField": REDACTED_PLACEHOLDER,
        "profile": REDACTED_PLACEHOLDER,
        "empty": "",
    }


def _store(auth: dict | None = None) -> "InMemoryConfigService":  # noqa: F821 - imported where used
    """A real store: the route may save through set_config or a read-modify-write."""
    from app.api.routes.toolsets import _get_agent_auth_path
    from tests.support.in_memory_config import InMemoryConfigService

    cs = InMemoryConfigService()
    if auth is not None:
        cs.values[_get_agent_auth_path("i1", "a1")] = {"isAuthenticated": True, "auth": auth}
    return cs


def _saved_auth(cs: "InMemoryConfigService") -> dict:  # noqa: F821 - imported in _store
    from app.api.routes.toolsets import _get_agent_auth_path

    return cs.values[_get_agent_auth_path("i1", "a1")]["auth"]


async def _save_with_schema(body) -> "InMemoryConfigService":  # noqa: F821 - imported where used
    cs = _store({"apiToken": "old", "baseUrl": "https://acme.example", "email": "a@acme.example"})
    with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock), \
         patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
         patch("app.api.routes.toolsets._instance_for_credential_update", new_callable=AsyncMock, return_value=INSTANCE):
        await update_agent_toolset_credentials("a1", "i1", _request_with_schema(body), config_service=cs)
    return cs


@pytest.mark.asyncio
async def test_a_clear_address_sent_back_unchanged_keeps_the_masked_secret() -> None:
    cs = await _save_with_schema({"auth": {
        "apiToken": REDACTED_PLACEHOLDER, "baseUrl": "https://acme.example", "email": "new@acme.example",
    }})
    assert _saved_auth(cs) == {
        "apiToken": "old", "baseUrl": "https://acme.example", "email": "new@acme.example",
    }


@pytest.mark.asyncio
async def test_a_new_address_beside_a_masked_secret_is_refused() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _save_with_schema({"auth": {"apiToken": REDACTED_PLACEHOLDER, "baseUrl": "https://evil.example"}})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_saving_the_masked_form_back_keeps_the_stored_value() -> None:
    cs = _store({"apiToken": "old", "user": "a"})
    body = {"auth": {"apiToken": REDACTED_PLACEHOLDER, "user": "b"}}
    with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock), \
         patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
         patch("app.api.routes.toolsets._instance_for_credential_update", new_callable=AsyncMock, return_value=INSTANCE), \
         patch("app.api.routes.toolsets._refuse_undeclared_auth_fields"), \
         patch("app.api.routes.toolsets._declared_destination_fields", return_value=set()), \
         patch("app.api.routes.toolsets._declared_field_defaults", return_value={}):
        await update_agent_toolset_credentials("a1", "i1", _request(body), config_service=cs)
    assert _saved_auth(cs) == {"apiToken": "old", "user": "b"}


@pytest.mark.asyncio
async def test_authenticate_refuses_the_placeholder() -> None:
    from fastapi import HTTPException

    from app.api.routes.toolsets import authenticate_agent_toolset

    cs = _store()
    with patch("app.api.routes.toolsets._require_agent_edit_access", new_callable=AsyncMock), \
         patch("app.api.routes.toolsets._get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
         patch("app.api.routes.toolsets._load_toolset_instances", new_callable=AsyncMock, return_value=[INSTANCE]):
        with pytest.raises(HTTPException) as exc:
            await authenticate_agent_toolset("a1", "i1", _request({"auth": {"apiToken": REDACTED_PLACEHOLDER}}), config_service=cs)
    assert exc.value.status_code == 400
    assert cs.values == {}
