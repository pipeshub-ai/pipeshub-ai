"""Saving toolset credentials keeps only what the toolset declares.

A stored credential may hold the fields the toolset's schema lists for the instance's
auth type and nothing else, and an OAuth instance takes no saved credentials at all:
its tokens come from the OAuth flow.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.toolsets import (
    InvalidAuthConfigError,
    _declared_auth_fields,
    _instance_for_credential_update,
    _refuse_undeclared_auth_fields,
    authenticate_agent_toolset,
    authenticate_toolset_instance,
    update_agent_toolset_credentials,
    update_toolset_credentials,
)

_ROUTES = "app.api.routes.toolsets"
ORG = "o1"

API_TOKEN_INSTANCE = {"_id": "i1", "orgId": ORG, "authType": "API_TOKEN", "toolsetType": "jira"}
BASIC_INSTANCE = {"_id": "i1", "orgId": ORG, "authType": "BASIC_AUTH", "toolsetType": "mariadb"}
OAUTH_INSTANCE = {"_id": "i1", "orgId": ORG, "authType": "OAUTH", "toolsetType": "googledrive"}


def _request(auth: object, metadata: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": "u1", "orgId": ORG}
    req.headers = {"authorization": "Bearer test"}
    req.body = AsyncMock(return_value=json.dumps({"auth": auth}).encode())
    req.app.state.toolset_registry.get_toolset_metadata.return_value = metadata or _metadata()
    return req


def _metadata(schemas: dict | None = None, default: dict | None = None) -> dict:
    auth: dict = {
        "schemas": schemas
        if schemas is not None
        else {
            "API_TOKEN": {"fields": [{"name": "baseUrl"}, {"name": "email"}, {"name": "apiToken"}]},
            "BASIC_AUTH": {"fields": [{"name": "host"}, {"name": "port"}, {"name": "username"}, {"name": "password"}]},
            "OAUTH": {"fields": [{"name": "clientId"}, {"name": "clientSecret"}]},
        }
    }
    if default is not None:
        auth["schema"] = default
    return {"name": "jira", "isInternal": False, "config": {"auth": auth}}


def _config_service(stored: dict | None) -> AsyncMock:
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value=stored)
    cs.set_config = AsyncMock()
    return cs


class TestDeclaredAuthFields:
    def test_fields_of_the_instances_auth_type(self) -> None:
        assert _declared_auth_fields(_request({}), API_TOKEN_INSTANCE) == {"baseUrl", "email", "apiToken"}
        assert _declared_auth_fields(_request({}), BASIC_INSTANCE) == {"host", "port", "username", "password"}

    def test_auth_type_is_matched_case_insensitively(self) -> None:
        instance = {**API_TOKEN_INSTANCE, "authType": "api_token"}
        assert _declared_auth_fields(_request({}), instance) == {"baseUrl", "email", "apiToken"}

    def test_default_schema_is_used_when_the_type_has_none(self) -> None:
        metadata = _metadata(schemas={}, default={"fields": [{"name": "apiKey"}]})
        assert _declared_auth_fields(_request({}, metadata), API_TOKEN_INSTANCE) == {"apiKey"}

    def test_no_schema_declares_nothing(self) -> None:
        assert _declared_auth_fields(_request({}, _metadata(schemas={})), API_TOKEN_INSTANCE) == set()

    def test_malformed_field_entries_are_skipped(self) -> None:
        metadata = _metadata(schemas={"API_TOKEN": {"fields": ["apiToken", {"label": "no name"}, {"name": "apiToken"}]}})
        assert _declared_auth_fields(_request({}, metadata), API_TOKEN_INSTANCE) == {"apiToken"}

    def test_unknown_toolset_type_is_an_error(self) -> None:
        req = _request({})
        req.app.state.toolset_registry.get_toolset_metadata.return_value = None
        with pytest.raises(HTTPException) as exc:
            _declared_auth_fields(req, API_TOKEN_INSTANCE)
        assert exc.value.status_code == 404


class TestRefuseUndeclaredAuthFields:
    def test_declared_fields_pass(self) -> None:
        _refuse_undeclared_auth_fields(_request({}), API_TOKEN_INSTANCE, {"baseUrl": "https://x", "apiToken": "t"})

    @pytest.mark.parametrize("extra", ["tokenUrl", "authorizeUrl", "clientId", "clientSecret", "redirectUri", "anything"])
    def test_an_undeclared_field_is_refused_by_name(self, extra: str) -> None:
        with pytest.raises(HTTPException) as exc:
            _refuse_undeclared_auth_fields(_request({}), API_TOKEN_INSTANCE, {"apiToken": "t", extra: "v"})
        assert exc.value.status_code == 400
        assert extra in exc.value.detail

    def test_every_undeclared_field_is_listed(self) -> None:
        with pytest.raises(HTTPException) as exc:
            _refuse_undeclared_auth_fields(_request({}), API_TOKEN_INSTANCE, {"zeta": 1, "alpha": 2, "apiToken": "t"})
        assert exc.value.detail.endswith("alpha, zeta")

    @pytest.mark.parametrize("auth", [["apiToken"], "apiToken", 5])
    def test_auth_that_is_not_an_object_is_refused(self, auth: object) -> None:
        with pytest.raises(HTTPException) as exc:
            _refuse_undeclared_auth_fields(_request({}), API_TOKEN_INSTANCE, auth)
        assert exc.value.status_code == 400


class TestInstanceForCredentialUpdate:
    async def test_returns_the_callers_instance(self) -> None:
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]):
            assert await _instance_for_credential_update("i1", ORG, AsyncMock()) == API_TOKEN_INSTANCE

    @pytest.mark.parametrize(
        "instances",
        [[], [{**API_TOKEN_INSTANCE, "_id": "other"}], [{**API_TOKEN_INSTANCE, "orgId": "another-org"}]],
        ids=["none", "other-id", "other-org"],
    )
    async def test_missing_instance_is_404(self, instances: list) -> None:
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
            with pytest.raises(HTTPException) as exc:
                await _instance_for_credential_update("i1", ORG, AsyncMock())
        assert exc.value.status_code == 404

    @pytest.mark.parametrize("auth_type", ["OAUTH", "oauth"])
    async def test_oauth_instance_is_refused(self, auth_type: str) -> None:
        instance = {**OAUTH_INSTANCE, "authType": auth_type}
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[instance]):
            with pytest.raises(HTTPException) as exc:
                await _instance_for_credential_update("i1", ORG, AsyncMock())
        assert exc.value.status_code == 400
        assert "OAuth" in exc.value.detail


async def _put_user(
    auth: object, instances: list, stored: dict | None, cs: AsyncMock | None = None
) -> tuple[dict, AsyncMock]:
    cs = cs or _config_service(stored)
    with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=instances):
        return await update_toolset_credentials("i1", _request(auth), cs), cs


async def _put_agent(
    auth: object, instances: list, stored: dict | None, cs: AsyncMock | None = None
) -> tuple[dict, AsyncMock]:
    cs = cs or _config_service(stored)
    with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=instances), patch(
        f"{_ROUTES}._require_agent_edit_access", new_callable=AsyncMock
    ):
        return await update_agent_toolset_credentials("agent-1", "i1", _request(auth), cs), cs


@pytest.mark.parametrize("put", [_put_user, _put_agent], ids=["user", "agent"])
class TestUpdateCredentials:
    async def test_declared_fields_are_saved(self, put) -> None:
        stored = {"isAuthenticated": True, "authType": "API_TOKEN", "auth": {"apiToken": "old"}, "credentials": {}}
        result, cs = await put({"baseUrl": "https://x", "apiToken": "new"}, [API_TOKEN_INSTANCE], stored)

        assert result["status"] == "success"
        saved = cs.set_config.await_args.args[1]
        assert saved["auth"] == {"baseUrl": "https://x", "apiToken": "new"}

    async def test_non_secret_declared_fields_are_saved(self, put) -> None:
        stored = {"isAuthenticated": True, "authType": "BASIC_AUTH", "auth": {}, "credentials": {}}
        auth = {"host": "db.internal", "port": "3306", "username": "u", "password": "p"}
        _, cs = await put(auth, [BASIC_INSTANCE], stored)
        assert cs.set_config.await_args.args[1]["auth"] == auth

    async def test_undeclared_field_is_refused_and_nothing_is_saved(self, put) -> None:
        stored = {"isAuthenticated": True, "authType": "API_TOKEN", "auth": {"apiToken": "old"}}
        cs = _config_service(stored)
        with pytest.raises(HTTPException) as exc:
            await put({"apiToken": "new", "tokenUrl": "https://elsewhere.example/t"}, [API_TOKEN_INSTANCE], stored, cs)
        assert exc.value.status_code == 400
        assert "tokenUrl" in exc.value.detail
        cs.set_config.assert_not_awaited()

    async def test_oauth_instance_is_refused(self, put) -> None:
        stored = {"isAuthenticated": True, "authType": "OAUTH", "oauthConfigId": "cfg1", "credentials": {"access_token": "a"}}
        with pytest.raises(HTTPException) as exc:
            await put({"tokenUrl": "https://elsewhere.example/t"}, [OAUTH_INSTANCE], stored)
        assert exc.value.status_code == 400
        assert "OAuth" in exc.value.detail

    async def test_missing_instance_is_404(self, put) -> None:
        with pytest.raises(HTTPException) as exc:
            await put({"apiToken": "new"}, [], {"auth": {}})
        assert exc.value.status_code == 404

    async def test_other_stored_keys_are_kept(self, put) -> None:
        stored = {"isAuthenticated": True, "authType": "API_TOKEN", "auth": {"apiToken": "old"}, "credentials": {"k": "v"}}
        _, cs = await put({"apiToken": "new"}, [API_TOKEN_INSTANCE], stored)
        saved = cs.set_config.await_args.args[1]
        assert saved["credentials"] == {"k": "v"}
        assert saved["isAuthenticated"] is True


class TestAuthenticateRefusesUndeclaredFields:
    async def test_user_authenticate(self) -> None:
        cs = _config_service(None)
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]):
            with pytest.raises(HTTPException) as exc:
                await authenticate_toolset_instance("i1", _request({"apiToken": "t", "tokenUrl": "https://e/t"}), cs)
        assert exc.value.status_code == 400
        cs.set_config.assert_not_awaited()

    async def test_agent_authenticate(self) -> None:
        cs = _config_service(None)
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]), patch(
            f"{_ROUTES}._require_agent_edit_access", new_callable=AsyncMock
        ):
            with pytest.raises(HTTPException) as exc:
                await authenticate_agent_toolset("agent-1", "i1", _request({"apiToken": "t", "clientSecret": "s"}), cs)
        assert exc.value.status_code == 400
        cs.set_config.assert_not_awaited()

    @pytest.mark.parametrize("auth_type", ["OAUTH", "oauth", "OAuth"])
    async def test_user_authenticate_on_an_oauth_instance_is_refused(self, auth_type: str) -> None:
        cs = _config_service(None)
        instance = {**OAUTH_INSTANCE, "authType": auth_type}
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[instance]):
            with pytest.raises(HTTPException) as exc:
                await authenticate_toolset_instance("i1", _request({"clientId": "c", "clientSecret": "s"}), cs)
        assert exc.value.status_code == 400
        assert "oauth/authorize" in exc.value.detail
        cs.set_config.assert_not_awaited()

    @pytest.mark.parametrize(
        ("instance", "auth", "missing"),
        [
            (API_TOKEN_INSTANCE, {"email": "a@b.c"}, "apiToken"),
            (API_TOKEN_INSTANCE, {"email": "a@b.c", "apiToken": None}, "apiToken"),
            (BASIC_INSTANCE, {"host": "db"}, "username and password"),
            (BASIC_INSTANCE, {"username": "u", "password": None}, "username and password"),
            (API_TOKEN_INSTANCE, {"apiToken": 123}, "apiToken"),
            (API_TOKEN_INSTANCE, {"apiToken": ["t"]}, "apiToken"),
            (API_TOKEN_INSTANCE, {"apiToken": {"value": "t"}}, "apiToken"),
            (API_TOKEN_INSTANCE, {"apiToken": "   "}, "apiToken"),
            (BASIC_INSTANCE, {"username": 7, "password": "p"}, "username and password"),
            (BASIC_INSTANCE, {"username": "u", "password": ["p"]}, "username and password"),
        ],
        ids=[
            "no-token", "null-token", "no-username-or-password", "null-password",
            "number-token", "list-token", "object-token", "blank-token", "number-username", "list-password",
        ],
    )
    async def test_authenticate_without_a_usable_required_value_says_which(
        self, instance: dict, auth: dict, missing: str
    ) -> None:
        cs = _config_service(None)
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[instance]):
            with pytest.raises(InvalidAuthConfigError, match=missing):
                await authenticate_toolset_instance("i1", _request(auth), cs)
            with patch(f"{_ROUTES}._require_agent_edit_access", new_callable=AsyncMock):
                with pytest.raises(InvalidAuthConfigError, match=missing):
                    await authenticate_agent_toolset("agent-1", "i1", _request(auth), cs)
        cs.set_config.assert_not_awaited()

    async def test_user_authenticate_with_declared_fields_saves(self) -> None:
        cs = _config_service(None)
        with patch(f"{_ROUTES}._load_toolset_instances", new_callable=AsyncMock, return_value=[API_TOKEN_INSTANCE]):
            result = await authenticate_toolset_instance(
                "i1", _request({"baseUrl": "https://x", "email": "a@b.c", "apiToken": "t"}), cs
            )
        assert result["isAuthenticated"] is True
        assert cs.set_config.await_args.args[1]["auth"]["apiToken"] == "t"
