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

from tests.unit.api.routes.test_toolsets import (
    assert_oauth_app_save_failed,
    oauth_app_save_failure,
)

if TYPE_CHECKING:
    from types import ModuleType

    from tests.support.in_memory_config import InMemoryConfigService

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


class TestSavedSecretStaysOnItsAddress:
    URLS = {"baseUrl"}
    STORED = {"baseUrl": "https://jira.example.com", "apiToken": SECRET}

    def test_new_address_with_a_masked_secret_is_refused(self, toolsets: ModuleType) -> None:
        with pytest.raises(HTTPException) as exc:
            toolsets._keep_stored_secrets({"baseUrl": "https://collect.example.net", "apiToken": MASK}, self.STORED, self.URLS)

        assert exc.value.status_code == 400
        assert exc.value.detail == "Enter the credentials again to use this new endpoint."

    def test_new_address_with_the_secret_typed_again_is_saved(self, toolsets: ModuleType) -> None:
        merged = toolsets._keep_stored_secrets(
            {"baseUrl": "https://other.example.net", "apiToken": "typed"}, self.STORED, self.URLS
        )

        assert merged == {"baseUrl": "https://other.example.net", "apiToken": "typed"}

    def test_same_address_keeps_the_saved_secret_however_it_is_written(self, toolsets: ModuleType) -> None:
        merged = toolsets._keep_stored_secrets(
            {"baseUrl": "HTTPS://Jira.Example.com/", "apiToken": MASK}, self.STORED, self.URLS
        )

        assert merged["apiToken"] == SECRET

    def test_masked_address_counts_as_unchanged(self, toolsets: ModuleType) -> None:
        merged = toolsets._keep_stored_secrets({"baseUrl": MASK, "apiToken": MASK}, self.STORED, self.URLS)

        assert merged == self.STORED


class TestSavedSecretStaysOnItsAddressThroughTheSavePaths:
    """The same rule, run through the org and personal save handlers."""

    FIELDS = [
        {"name": "host", "fieldType": "TEXT"},
        {"name": "port", "fieldType": "NUMBER"},
        {"name": "password", "fieldType": "PASSWORD"},
    ]
    STORED = {"host": "db.example.com", "port": 5432, "password": SECRET}

    def _request(self, auth: dict) -> MagicMock:
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.body = AsyncMock(return_value=json.dumps({"authConfig": auth}).encode())
        return request

    @staticmethod
    def _store(toolsets: ModuleType, instance: dict) -> InMemoryConfigService:
        """A real store holding the instance: the route may save through set_config or a read-modify-write."""
        from tests.support.in_memory_config import InMemoryConfigService

        store = InMemoryConfigService()
        store.values[toolsets._get_instances_path("o1")] = [dict(instance)]
        return store

    async def _org_update(self, toolsets: ModuleType, auth: dict) -> dict:
        instance = {"_id": "i1", "orgId": "o1", "toolsetType": "mariadb", "authType": "BASIC_AUTH",
                    "instanceName": "db", "auth": dict(self.STORED)}
        config_service = self._store(toolsets, instance)
        with (
            patch.object(toolsets, "_get_user_context", return_value={"user_id": "u1", "org_id": "o1"}),
            patch.object(toolsets.toolset_store, "get_personal_instance", AsyncMock(return_value=None)),
            patch.object(toolsets, "_check_user_is_admin", AsyncMock(return_value=True)),
            patch.object(toolsets, "_load_instances_for_mutation", AsyncMock(return_value=[instance])),
            patch.object(toolsets, "_declared_schema_fields", return_value=self.FIELDS),
        ):
            await toolsets.update_toolset_instance("i1", self._request(auth), config_service=config_service)
        return config_service.values[toolsets._get_instances_path("o1")][0]

    @pytest.mark.parametrize("auth", [
        {"host": "evil.example.net", "port": 5432, "password": MASK},
        {"host": "db.example.com", "port": 9999, "password": MASK},
    ], ids=["host", "port"])
    async def test_org_update_refuses_a_moved_server_with_the_masked_secret(self, toolsets: ModuleType, auth: dict) -> None:
        with pytest.raises(HTTPException) as exc:
            await self._org_update(toolsets, auth)

        assert exc.value.status_code == 400
        assert exc.value.detail == "Enter the credentials again to use this new endpoint."

    async def test_org_update_accepts_a_moved_server_when_the_secret_is_typed_again(self, toolsets: ModuleType) -> None:
        instance = await self._org_update(toolsets, {"host": "new.example.com", "port": 5432, "password": "typed"})

        assert instance["auth"] == {"host": "new.example.com", "port": 5432, "password": "typed"}

    async def test_org_update_keeps_the_secret_for_the_same_server(self, toolsets: ModuleType) -> None:
        instance = await self._org_update(toolsets, {"host": "DB.example.com", "port": "5432", "password": MASK})

        assert instance["auth"]["password"] == SECRET

    @pytest.mark.parametrize(("port", "refused"), [("445", False), ("9999", True)])
    async def test_org_update_treats_the_schema_default_as_no_change_when_none_is_stored(
        self, toolsets: ModuleType, port: str, refused: bool
    ) -> None:
        fields = [{"name": "port", "fieldType": "NUMBER", "defaultValue": "445"}, {"name": "password", "fieldType": "PASSWORD"}]
        instance = {"_id": "i1", "orgId": "o1", "toolsetType": "smb", "authType": "BASIC_AUTH",
                    "instanceName": "s", "auth": {"password": SECRET}}
        with (
            patch.object(toolsets, "_get_user_context", return_value={"user_id": "u1", "org_id": "o1"}),
            patch.object(toolsets.toolset_store, "get_personal_instance", AsyncMock(return_value=None)),
            patch.object(toolsets, "_check_user_is_admin", AsyncMock(return_value=True)),
            patch.object(toolsets, "_load_instances_for_mutation", AsyncMock(return_value=[instance])),
            patch.object(toolsets, "_declared_schema_fields", return_value=fields),
        ):
            config_service = self._store(toolsets, instance)
            call = toolsets.update_toolset_instance(
                "i1", self._request({"port": port, "password": MASK}), config_service=config_service
            )
            if refused:
                with pytest.raises(HTTPException):
                    await call
            else:
                await call
                saved = config_service.values[toolsets._get_instances_path("o1")][0]
                assert saved["auth"] == {"port": "445", "password": SECRET}

    async def test_personal_update_refuses_a_moved_server_with_the_masked_secret(self, toolsets: ModuleType) -> None:
        instance = {"_id": "i1", "orgId": "o1", "createdBy": "u1", "toolsetType": "zendesk", "authType": "API_TOKEN",
                    "instanceName": "z", "scope": "personal", "auth": {"subdomain": "acme", "apiToken": SECRET}}
        fields = [{"name": "subdomain", "fieldType": "TEXT"}, {"name": "apiToken", "fieldType": "PASSWORD"}]
        validated = AsyncMock()
        with (
            patch.object(toolsets, "_declared_schema_fields", return_value=fields),
            patch.object(toolsets, "_validated_personal_settings", validated),
        ):
            with pytest.raises(HTTPException) as exc:
                await toolsets._update_personal_instance(
                    self._request({"subdomain": "evil", "apiToken": MASK}), instance, MagicMock()
                )

        assert exc.value.status_code == 400
        validated.assert_not_awaited()


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

    @pytest.mark.parametrize("key", ["tokenUrl", "authorizeUrl", "instanceUrl"])
    async def test_new_address_with_the_saved_secret_is_refused(self, toolsets: ModuleType, key: str) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientId": "id", "clientSecret": SECRET, key: "https://a.example.com/x"}}]

        with pytest.raises(HTTPException) as exc:
            await self._save(toolsets, {"clientId": "id", "clientSecret": MASK, key: "https://collect.example.net/x"}, stored, "cfg")

        assert exc.value.status_code == 400
        assert "Enter the credentials again" in exc.value.detail
        assert stored[0]["config"][key] == "https://a.example.com/x"

    async def test_new_address_with_the_secret_typed_again_is_saved(self, toolsets: ModuleType) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientSecret": SECRET, "tokenUrl": "https://a.example.com/t"}}]

        await self._save(toolsets, {"clientSecret": "typed", "tokenUrl": "https://b.example.com/t"}, stored, "cfg")

        assert stored[0]["config"] == {"clientSecret": "typed", "tokenUrl": "https://b.example.com/t"}

    async def test_another_spelling_of_the_secret_does_not_stand_in_for_the_saved_one(self, toolsets: ModuleType) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientSecret": SECRET, "tokenUrl": "https://a.example.com/t"}}]

        with pytest.raises(HTTPException):
            await self._save(toolsets, {"client_secret": "decoy", "tokenUrl": "https://collect.example.net/x"}, stored, "cfg")

    async def test_an_unchanged_registry_default_is_not_a_change(self, toolsets: ModuleType) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientSecret": SECRET}}]
        registry_default = MagicMock(authorize_url="https://auth.example.com/a", token_url="https://auth.example.com/t")

        with patch.object(toolsets, "_get_oauth_config_from_registry", return_value=registry_default):
            await self._save(toolsets, {"clientSecret": MASK, "tokenUrl": "https://auth.example.com/t"}, stored, "cfg")
            with pytest.raises(HTTPException):
                await self._save(toolsets, {"clientSecret": MASK, "tokenUrl": "https://collect.example.net/t"}, stored, "cfg")

        assert stored[0]["config"]["clientSecret"] == SECRET

    async def test_same_address_keeps_the_saved_secret(self, toolsets: ModuleType) -> None:
        stored = [{"_id": "cfg", "orgId": "o1", "config": {"clientSecret": SECRET, "tokenUrl": "https://a.example.com/t"}}]

        await self._save(toolsets, {"clientSecret": MASK, "tokenUrl": "https://A.example.com/t/"}, stored, "cfg")

        assert stored[0]["config"]["clientSecret"] == SECRET


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
                config_service, oauth_save=AsyncMock(side_effect=oauth_app_save_failure()),
            )

        assert_oauth_app_save_failed(exc)
        config_service.set_config.assert_not_called()

    async def test_unexpected_error_while_saving_the_app_refuses(self, toolsets: ModuleType) -> None:
        """The real save helper turns an unexpected error (here a non-string tenantId) into a 500."""
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
                 patch.object(toolsets, "_create_or_update_toolset_oauth_config",
                              new=AsyncMock(side_effect=oauth_app_save_failure())):
                await toolsets.update_toolset_instance(
                    "i1", _request({"baseUrl": "https://app.example.com", "authConfig": {"clientId": "cid", "clientSecret": SECRET}}),
                    config_service=config_service,
                )

        assert exc.value.status_code == 500
        config_service.set_config.assert_not_called()

