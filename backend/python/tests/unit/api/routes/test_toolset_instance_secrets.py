"""An instance's inline `auth` holds credentials: non-admins never receive it, admins get it
masked, and a masked value echoed back on update must never overwrite the stored secret
or be saved as a new one."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

if TYPE_CHECKING:
    from types import ModuleType

MASK = "********"
SECRET = "s3cret"


@pytest.fixture
def toolsets(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # The routes module only loads cleanly once app.edition_config is imported first.
    import app.edition_config  # noqa: F401
    from app.api.routes import toolsets as module

    monkeypatch.setattr(
        module, "mask_oauth_secrets",
        lambda cfg, **_: {k: (MASK if k in {"clientSecret", "apiKey"} else v) for k, v in cfg.items()},
    )
    monkeypatch.setattr(module, "is_redacted_placeholder", lambda v: v == MASK)
    monkeypatch.setattr(module, "REDACTED_PLACEHOLDER", MASK)
    return module


class TestInstanceForResponse:
    def test_admin_gets_inline_auth_masked(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "auth": {"clientId": "cid", "clientSecret": SECRET}}

        safe = toolsets._instance_for_response(instance, is_admin=True)

        assert safe["auth"] == {"clientId": "cid", "clientSecret": MASK}
        assert SECRET not in str(safe)

    def test_nested_object_is_hidden_whole(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "auth": {"baseUrl": "https://jira.example.com", "credentials": {"apiToken": SECRET}}}

        safe = toolsets._instance_for_response(instance, is_admin=True)

        assert safe["auth"] == {"baseUrl": "https://jira.example.com", "credentials": MASK}

    def test_list_is_hidden_only_when_it_holds_an_object(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "auth": {"scopes": ["read", "write"], "accounts": [{"password": SECRET}]}}

        safe = toolsets._instance_for_response(instance, is_admin=True)

        assert safe["auth"] == {"scopes": ["read", "write"], "accounts": MASK}

    def test_non_admin_gets_no_inline_auth_at_all(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "instanceName": "n", "auth": {"clientId": "cid", "clientSecret": SECRET}}

        safe = toolsets._instance_for_response(instance, is_admin=False)

        assert safe == {"_id": "i1", "instanceName": "n"}

    def test_stored_instance_is_not_mutated(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "auth": {"clientSecret": SECRET}}

        toolsets._instance_for_response(instance, is_admin=True)
        toolsets._instance_for_response(instance, is_admin=False)

        assert instance["auth"]["clientSecret"] == SECRET

    def test_instance_without_inline_auth_passes_through(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "oauthConfigId": "cfg"}

        assert toolsets._instance_for_response(instance, is_admin=False) == instance


class TestKeepStoredSecrets:
    def test_masked_value_keeps_the_stored_secret(self, toolsets: ModuleType) -> None:
        merged = toolsets._keep_stored_secrets(
            {"clientId": "new-id", "clientSecret": MASK}, {"clientId": "old-id", "clientSecret": SECRET}
        )

        assert merged == {"clientId": "new-id", "clientSecret": SECRET}

    def test_new_secret_replaces_the_stored_one(self, toolsets: ModuleType) -> None:
        assert toolsets._keep_stored_secrets({"clientSecret": "rotated"}, {"clientSecret": SECRET}) == {
            "clientSecret": "rotated"
        }

    def test_masked_nested_object_keeps_the_stored_one(self, toolsets: ModuleType) -> None:
        stored = {"baseUrl": "old", "credentials": {"apiToken": SECRET}}

        merged = toolsets._keep_stored_secrets({"baseUrl": "new", "credentials": MASK}, stored)

        assert merged == {"baseUrl": "new", "credentials": {"apiToken": SECRET}}

    def test_masked_value_without_a_stored_secret_is_dropped(self, toolsets: ModuleType) -> None:
        assert toolsets._keep_stored_secrets({"clientSecret": MASK}, None) == {}


class TestOAuthConfigSave:
    async def _save(
        self, toolsets: ModuleType, auth_config: dict, stored: list[dict], oauth_config_id: str | None
    ) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=stored)
        config_service.set_config = AsyncMock()
        with patch.object(toolsets, "_prepare_toolset_auth_config", new=AsyncMock(side_effect=lambda cfg, *_: cfg)):
            await toolsets._create_or_update_toolset_oauth_config(
                toolset_type="jira",
                auth_config={"type": "OAUTH", **auth_config},
                instance_name="Jira",
                user_id="u1",
                org_id="o1",
                config_service=config_service,
                registry=MagicMock(),
                base_url="https://app.example.com",
                oauth_config_id=oauth_config_id,
            )

    @pytest.mark.parametrize("key", ["clientSecret", "client_secret", "clientsecret"])
    async def test_masked_secret_keeps_the_stored_one_in_every_spelling(self, toolsets: ModuleType, key: str) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientId": "old-id", key: SECRET}}]

        await self._save(toolsets, {"clientId": "new-id", key: MASK}, stored, "cfg")

        assert stored[0]["config"] == {"clientId": "new-id", key: SECRET}

    @pytest.mark.parametrize("oauth_config_id", [None, "inherited-or-deleted"])
    async def test_masked_value_is_never_saved_as_a_new_config(
        self, toolsets: ModuleType, oauth_config_id: str | None
    ) -> None:
        stored: list[dict] = []

        with pytest.raises(HTTPException) as exc:
            await self._save(toolsets, {"clientId": "cid", "clientSecret": MASK}, stored, oauth_config_id)

        assert exc.value.status_code == 400
        assert stored == []


class TestListInstances:
    async def _list(self, toolsets: ModuleType, *, is_admin: bool) -> list[dict]:
        instances = [{"_id": "i1", "orgId": "o1", "instanceName": "n", "toolsetType": "jira",
                      "auth": {"clientSecret": SECRET}}]
        registry = MagicMock()
        registry.get_toolset_metadata.return_value = {}
        with patch.object(toolsets, "_get_user_context", return_value={"user_id": "u1", "org_id": "o1"}), \
             patch.object(toolsets, "_load_toolset_instances", new=AsyncMock(return_value=instances)), \
             patch.object(toolsets, "_get_registry", return_value=registry), \
             patch.object(toolsets, "_check_user_is_admin", new=AsyncMock(return_value=is_admin)):
            result = await toolsets.get_toolset_instances(
                MagicMock(), page=1, limit=50, search=None, config_service=MagicMock()
            )
        return result["instances"]

    async def test_member_listing_carries_no_credentials(self, toolsets: ModuleType) -> None:
        listed = await self._list(toolsets, is_admin=False)

        assert "auth" not in listed[0]
        assert SECRET not in str(listed)

    async def test_admin_listing_is_masked(self, toolsets: ModuleType) -> None:
        listed = await self._list(toolsets, is_admin=True)

        assert listed[0]["auth"] == {"clientSecret": MASK}


@pytest.fixture
def secrets_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIDE_SECRET_CONFIG", "true")


@pytest.mark.usefixtures("secrets_hidden")
class TestWithTheRealEditionMasker:
    """No masker monkeypatching: with HIDE_SECRET_CONFIG=true either edition must hide the secret."""

    def test_admin_response_never_carries_the_secret(self) -> None:
        import app.edition_config as edition
        from app.api.routes import toolsets

        safe = toolsets._instance_for_response(
            {"_id": "i1", "auth": {"clientId": "cid", "clientSecret": SECRET}}, is_admin=True
        )

        assert SECRET not in str(safe)
        assert safe["auth"] == {"clientId": "cid", "clientSecret": edition.REDACTED_PLACEHOLDER}

    def test_admin_response_never_carries_a_nested_secret(self) -> None:
        from app.api.routes import toolsets

        safe = toolsets._instance_for_response(
            {"_id": "i1", "auth": {"credentials": {"clientSecret": SECRET, "apiToken": SECRET}}}, is_admin=True
        )

        assert SECRET not in str(safe)

    def test_echoing_the_masked_value_back_keeps_the_stored_secret(self) -> None:
        import app.edition_config as edition
        from app.api.routes import toolsets

        merged = toolsets._keep_stored_secrets(
            {"clientSecret": edition.REDACTED_PLACEHOLDER}, {"clientSecret": SECRET}
        )

        assert merged == {"clientSecret": SECRET}


def _request(body: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1"}
    request.query_params.getlist.return_value = []
    request.body = AsyncMock(return_value=json.dumps(body or {}).encode())
    request.app.state.toolset_registry.get_toolset_metadata.return_value = {
        "supported_auth_types": ["OAUTH", "API_TOKEN"], "tools": [],
    }
    return request


def _config_service() -> MagicMock:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=[])
    config_service.set_config = AsyncMock()
    return config_service


def _stored(config_service: MagicMock) -> str:
    """Everything the handler wrote to the config store."""
    return str([c.args for c in config_service.set_config.call_args_list])


class TestGetInstance:
    async def _get(self, toolsets: ModuleType, *, is_admin: bool) -> dict:
        instance = {"_id": "i1", "orgId": "o1", "instanceName": "n", "toolsetType": "jiradatacenter",
                    "authType": "API_TOKEN", "auth": {"baseUrl": "https://jira.example.com", "apiKey": SECRET}}
        with patch.object(toolsets, "_load_toolset_instances", new=AsyncMock(return_value=[instance])), \
             patch.object(toolsets, "_check_user_is_admin", new=AsyncMock(return_value=is_admin)):
            result = await toolsets.get_toolset_instance("i1", _request(), config_service=_config_service())
        return result["instance"]

    async def test_member_gets_no_inline_auth(self, toolsets: ModuleType) -> None:
        instance = await self._get(toolsets, is_admin=False)

        assert "auth" not in instance
        assert SECRET not in str(instance)

    async def test_admin_gets_the_token_masked(self, toolsets: ModuleType) -> None:
        instance = await self._get(toolsets, is_admin=True)

        assert instance["auth"] == {"baseUrl": "https://jira.example.com", "apiKey": MASK}


class TestCreateNeverStoresAnOAuthSecretInline:
    """An OAuth client secret belongs in its OAuth app, never in the shared instance list."""

    async def _create(
        self, toolsets: ModuleType, body: dict, config_service: MagicMock, *, oauth_save: AsyncMock | None = None,
    ) -> dict:
        save = nullcontext() if oauth_save is None else patch.object(
            toolsets, "_create_or_update_toolset_oauth_config", new=oauth_save
        )
        with patch.object(toolsets, "_check_user_is_admin", new=AsyncMock(return_value=True)), \
             patch.object(toolsets, "_load_instances_for_mutation", new=AsyncMock(return_value=[])), save:
            return await toolsets.create_toolset_instance(_request(body), config_service=config_service)

    @pytest.mark.parametrize("body", [
        {"authConfig": {"clientId": "cid", "clientSecret": SECRET}},
        {"clientId": "cid", "clientSecret": SECRET},
    ])
    async def test_failed_oauth_app_save_refuses_and_stores_nothing(self, toolsets: ModuleType, body: dict) -> None:
        config_service = _config_service()

        with pytest.raises(HTTPException) as exc:
            await self._create(
                toolsets,
                {"instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH", "baseUrl": "https://app.example.com", **body},
                config_service, oauth_save=AsyncMock(return_value=None),
            )

        assert exc.value.status_code == 500
        config_service.set_config.assert_not_called()

    async def test_unexpected_error_while_saving_the_app_refuses(self, toolsets: ModuleType) -> None:
        """The real save helper turns an unexpected error (here a non-string tenantId) into None."""
        config_service = _config_service()
        body = {"instanceName": "Outlook", "toolsetType": "outlook", "authType": "OAUTH", "baseUrl": "https://app.example.com",
                "authConfig": {"clientId": "cid", "clientSecret": SECRET, "tenantId": 42}}

        with pytest.raises(HTTPException) as exc, \
             patch.object(toolsets, "_get_oauth_config_from_registry", return_value=MagicMock(redirect_uri="")):
            await self._create(toolsets, body, config_service)

        assert exc.value.status_code == 500
        assert SECRET not in _stored(config_service)

    async def test_saved_oauth_app_is_linked_not_copied(self, toolsets: ModuleType) -> None:
        config_service = _config_service()
        result = await self._create(
            toolsets,
            {"instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH", "baseUrl": "https://app.example.com",
             "authConfig": {"clientId": "cid", "clientSecret": SECRET}},
            config_service, oauth_save=AsyncMock(return_value="cfg-1"),
        )

        saved = config_service.set_config.call_args.args[1][0]
        assert saved["oauthConfigId"] == "cfg-1"
        assert "auth" not in saved
        assert SECRET not in str(result)

    async def test_oauth_instance_without_credentials_has_no_inline_auth(self, toolsets: ModuleType) -> None:
        oauth_save, config_service = AsyncMock(), _config_service()
        await self._create(
            toolsets,
            {"instanceName": "Jira", "toolsetType": "jira", "authType": "OAUTH", "baseUrl": "https://app.example.com"},
            config_service, oauth_save=oauth_save,
        )

        oauth_save.assert_not_called()
        assert "auth" not in config_service.set_config.call_args.args[1][0]

    async def test_non_oauth_instance_still_keeps_its_settings(self, toolsets: ModuleType) -> None:
        config_service = _config_service()
        await self._create(
            toolsets,
            {"instanceName": "Jira DC", "toolsetType": "jiradatacenter", "authType": "API_TOKEN",
             "authConfig": {"baseUrl": "https://jira.example.com"}},
            config_service, oauth_save=AsyncMock(),
        )

        assert config_service.set_config.call_args.args[1][0]["auth"] == {"baseUrl": "https://jira.example.com"}


class TestUpdateOAuthAppSaveFailure:
    async def test_failed_save_refuses_and_leaves_the_instance_alone(self, toolsets: ModuleType) -> None:
        instances = [{"_id": "i1", "orgId": "o1", "instanceName": "Jira", "toolsetType": "jira",
                      "authType": "OAUTH", "oauthConfigId": "cfg-1"}]
        config_service = _config_service()

        with pytest.raises(HTTPException) as exc:
            with patch.object(toolsets, "_check_user_is_admin", new=AsyncMock(return_value=True)), \
                 patch.object(toolsets, "_load_instances_for_mutation", new=AsyncMock(return_value=instances)), \
                 patch.object(toolsets, "_create_or_update_toolset_oauth_config", new=AsyncMock(return_value=None)):
                await toolsets.update_toolset_instance(
                    "i1", _request({"baseUrl": "https://app.example.com", "authConfig": {"clientId": "cid", "clientSecret": SECRET}}),
                    config_service=config_service,
                )

        assert exc.value.status_code == 500
        config_service.set_config.assert_not_called()
