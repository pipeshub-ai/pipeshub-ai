"""A person's own OAuth app for their own personal connector: only they may use it.

Members add personal OAuth connectors with their own client ID and secret, which become a
personal OAuth app (``ownerUserId`` set). These tests run the connector and OAuth app routes
against an in-memory config store and pin who may list, link, change and sign in with it.
"""

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router as router_module
from app.connectors.api.router import (
    _build_oauth_flow_config,
    create_connector_instance,
    get_all_oauth_configs,
    get_oauth_config_by_id,
    list_oauth_configs,
    update_connector_instance_auth_config,
    update_connector_instance_config,
    update_oauth_config,
)

# Importing a connector registers its OAuth fields (client ID and secret, both required).
from app.connectors.sources.google.drive.individual import (
    connector as _drive,  # noqa: F401
)
from app.utils.oauth_config import (
    OAUTH_APP_OWNER_FIELD,
    oauth_app_public_only,
    remove_personal_oauth_apps,
)

ORG_ID = "org-1"
ALICE = "user-alice"
BOB = "user-bob"
ADMIN = "user-admin"
APPS_PATH = "/services/oauth/drive"
CONNECTOR_ID = "conn-1"
CONFIG_PATH = f"/services/connectors/{CONNECTOR_ID}/config"
ORG_APP_SECRET = "org-secret-that-must-not-leak"
ALICE_SECRET = "alice-secret-that-must-not-leak"


class InMemoryConfigService:
    """Stands in for ConfigurationService: values go in and come out as copies."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = copy.deepcopy(values or {})
        self.values.setdefault("/services/endpoints", {"frontend": {"publicEndpoint": "https://pipeshub.example"}})

    async def get_config(self, path: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        del use_cache
        return copy.deepcopy(self.values.get(path, default))

    async def set_config(self, path: str, value: object) -> bool:
        self.values[path] = copy.deepcopy(value)
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [key for key in self.values if key.startswith(directory)]

    def apps(self) -> list[dict[str, Any]]:
        return self.values.get(APPS_PATH) or []


def _org_app(app_id: str = "org-app", name: str = "Company Drive") -> dict[str, Any]:
    return {
        "_id": app_id,
        "oauthInstanceName": name,
        "connectorType": "Drive",
        "orgId": ORG_ID,
        "createdBy": ADMIN,
        "config": {"clientId": "org-client", "clientSecret": ORG_APP_SECRET},
    }


def _personal_app(owner: str, app_id: str, secret: str = ALICE_SECRET, name: str = "My app") -> dict[str, Any]:
    return {
        "_id": app_id,
        "oauthInstanceName": name,
        "connectorType": "Drive",
        "orgId": ORG_ID,
        "createdBy": owner,
        OAUTH_APP_OWNER_FIELD: owner,
        "config": {"clientId": f"{owner}-client", "clientSecret": secret},
    }


def _metadata() -> dict[str, Any]:
    return {
        "scope": ["personal"],
        "supportedAuthTypes": ["OAUTH"],
        "config": {
            "auth": {
                "schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/Drive", "fields": []}},
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://accounts.google.com/o/oauth2/v2/auth",
                        "tokenUrl": "https://oauth2.googleapis.com/token",
                        "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
                    }
                },
            }
        },
    }


def _request(user_id: str, *, admin: bool = False, body: dict[str, Any] | None = None, store: InMemoryConfigService | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": ORG_ID, "role": "admin" if admin else "member"}
    request.json = AsyncMock(return_value=body or {})
    request.headers = {}
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test_personal_oauth_apps"))
    request.app.container.config_service = MagicMock(return_value=store)
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=_metadata())
    registry.create_connector_instance_on_configuration = AsyncMock(
        return_value={"_key": CONNECTOR_ID, "type": "Drive"}
    )
    registry.update_connector_instance = AsyncMock(return_value={"_key": CONNECTOR_ID})
    registry._normalize_connector_name = MagicMock(return_value="drive")
    registry._get_beta_connector_names = MagicMock(return_value=[])
    request.app.state.connector_registry = registry
    return request


async def _create(store: InMemoryConfigService, user_id: str, auth: dict[str, Any], *, admin: bool = False, scope: str = "personal") -> dict[str, Any]:
    body = {
        "connectorType": "Drive",
        "instanceName": "My Drive",
        "scope": scope,
        "authType": "OAUTH",
        "config": {"auth": auth},
    }
    graph = AsyncMock()
    graph.get_account_type = AsyncMock(return_value="individual")
    with (
        patch.object(router_module, "resolve_config_service", return_value=store),
        patch.object(router_module, "check_beta_connector_access", AsyncMock()),
        patch.object(router_module, "_store_config_action_items", AsyncMock()),
    ):
        return await create_connector_instance(_request(user_id, admin=admin, body=body, store=store), graph)


def _instance(owner: str = ALICE, scope: str = "personal") -> dict[str, Any]:
    return {
        "_key": CONNECTOR_ID,
        "type": "Drive",
        "name": "My Drive",
        "isActive": False,
        "authType": "OAUTH",
        "scope": scope,
        "createdBy": owner,
    }


async def _save_auth(store: InMemoryConfigService, user_id: str, auth: dict[str, Any], *, admin: bool = False, scope: str = "personal") -> dict[str, Any]:
    request = _request(user_id, admin=admin, body={"auth": auth}, store=store)
    with (
        patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=_instance(user_id, scope))),
        patch.object(router_module, "resolve_config_service", return_value=store),
    ):
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"scope": scope})
        return await update_connector_instance_auth_config(CONNECTOR_ID, request, graph)


async def _save_full(store: InMemoryConfigService, user_id: str, auth: dict[str, Any], *, admin: bool = False) -> dict[str, Any]:
    request = _request(user_id, admin=admin, body={"auth": auth}, store=store)
    with (
        patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=_instance(user_id))),
        patch.object(router_module, "resolve_config_service", return_value=store),
    ):
        return await update_connector_instance_config(CONNECTOR_ID, request)


def _linked_app_id(store: InMemoryConfigService) -> str | None:
    return (store.values.get(CONFIG_PATH) or {}).get("auth", {}).get("oauthConfigId")


@pytest.mark.asyncio
class TestCreatingAPersonalApp:
    async def test_a_member_s_client_credentials_become_their_own_app(self) -> None:
        store = InMemoryConfigService()

        await _create(store, ALICE, {"clientId": "alice-client", "clientSecret": ALICE_SECRET})

        [app] = store.apps()
        assert app[OAUTH_APP_OWNER_FIELD] == ALICE
        assert app["config"] == {"clientId": "alice-client", "clientSecret": ALICE_SECRET}
        assert _linked_app_id(store) == app["_id"]
        # The connector keeps no copy of the secret.
        assert "clientSecret" not in store.values[CONFIG_PATH]["auth"]

    async def test_an_admin_s_personal_connector_makes_a_personal_app_too(self) -> None:
        store = InMemoryConfigService()

        await _create(store, ADMIN, {"clientId": "admin-client", "clientSecret": "s"}, admin=True)

        assert store.apps()[0][OAUTH_APP_OWNER_FIELD] == ADMIN

    async def test_an_admin_s_team_connector_makes_an_org_app(self) -> None:
        store = InMemoryConfigService()
        request_metadata = _metadata()
        request_metadata["scope"] = ["team"]
        body_auth = {"clientId": "org-client", "clientSecret": "s"}
        req = _request(ADMIN, admin=True, store=store, body={
            "connectorType": "Drive", "instanceName": "Team Drive", "scope": "team",
            "authType": "OAUTH", "config": {"auth": body_auth},
        })
        req.app.state.connector_registry.get_connector_metadata = AsyncMock(return_value=request_metadata)
        graph = AsyncMock()
        graph.get_account_type = AsyncMock(return_value="individual")
        with (
            patch.object(router_module, "resolve_config_service", return_value=store),
            patch.object(router_module, "check_beta_connector_access", AsyncMock()),
            patch.object(router_module, "_store_config_action_items", AsyncMock()),
        ):
            await create_connector_instance(req, graph)

        assert OAUTH_APP_OWNER_FIELD not in store.apps()[0]

    async def test_a_client_id_without_its_secret_is_refused_before_anything_exists(self) -> None:
        store = InMemoryConfigService()

        with pytest.raises(HTTPException) as exc:
            await _create(store, ALICE, {"clientId": "alice-client"})

        assert exc.value.status_code == 400
        assert store.apps() == []
        assert CONFIG_PATH not in store.values

    async def test_a_member_must_choose_an_app_or_enter_their_own(self) -> None:
        store = InMemoryConfigService()

        with pytest.raises(HTTPException) as exc:
            await _create(store, ALICE, {})

        assert exc.value.status_code == 400

    async def test_names_are_unique_per_owner_only(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_org_app(name="Drive"), _personal_app(BOB, "bob-app", name="Drive")]})

        await _create(store, ALICE, {"clientId": "a", "clientSecret": "b", "oauthInstanceName": "Drive"})
        with pytest.raises(HTTPException) as exc:
            await _create(store, ALICE, {"clientId": "c", "clientSecret": "d", "oauthInstanceName": "Drive"})

        assert exc.value.status_code == 409
        assert len(store.apps()) == 3

    async def test_a_personal_app_never_becomes_the_org_default(self) -> None:
        store = InMemoryConfigService()
        with patch.object(router_module, "ensure_oauth_default") as ensure_default:
            await _create(store, ALICE, {"clientId": "a", "clientSecret": "b"})

        ensure_default.assert_not_called()


@pytest.mark.asyncio
class TestLinkingSomeoneElsesApp:
    async def test_a_teammate_s_app_is_refused_like_a_missing_one(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        with pytest.raises(HTTPException) as theirs:
            await _create(store, BOB, {"oauthConfigId": "alice-app"})
        with pytest.raises(HTTPException) as missing:
            await _create(store, BOB, {"oauthConfigId": "no-such-app"})

        assert theirs.value.status_code == missing.value.status_code == 404
        assert theirs.value.detail.replace("alice-app", "X") == missing.value.detail.replace("no-such-app", "X")
        assert CONFIG_PATH not in store.values

    async def test_an_admin_can_not_link_a_member_s_app(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        with pytest.raises(HTTPException) as exc:
            await _create(store, ADMIN, {"oauthConfigId": "alice-app"}, admin=True)

        assert exc.value.status_code == 404

    async def test_full_credentials_with_a_teammate_s_app_id_make_a_new_app_of_your_own(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        await _create(store, BOB, {"oauthConfigId": "alice-app", "clientId": "bob-client", "clientSecret": "bob-secret"})

        alice_app, bob_app = store.apps()
        assert alice_app == _personal_app(ALICE, "alice-app")
        assert bob_app[OAUTH_APP_OWNER_FIELD] == BOB
        assert bob_app["config"] == {"clientId": "bob-client", "clientSecret": "bob-secret"}

    async def test_the_owner_reuses_their_app_on_another_connector(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        await _create(store, ALICE, {"oauthConfigId": "alice-app"})

        assert _linked_app_id(store) == "alice-app"
        assert len(store.apps()) == 1

    async def test_a_member_still_links_an_org_app(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_org_app()]})

        await _create(store, ALICE, {"oauthConfigId": "org-app"})

        assert _linked_app_id(store) == "org-app"


@pytest.mark.asyncio
class TestNoCopyOfAnOrgApp:
    async def test_repeating_the_org_app_s_values_writes_nothing(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_org_app()]})

        await _create(store, ALICE, {"oauthConfigId": "org-app", "clientId": "org-client"})

        assert store.apps() == [_org_app()]
        assert _linked_app_id(store) == "org-app"

    async def test_less_than_a_whole_client_against_an_org_app_changes_nothing(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_org_app()]})

        await _create(store, ALICE, {"oauthConfigId": "org-app", "clientId": "alice-client"})

        assert store.apps() == [_org_app()]
        assert _linked_app_id(store) == "org-app"

    async def test_the_full_save_never_takes_the_org_secret_into_a_personal_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "clientId": "org-client", "connectorScope": "personal"}},
        })

        with pytest.raises(HTTPException) as exc:
            await _save_full(store, ALICE, {"clientId": "alice-client"})

        assert exc.value.status_code == 400
        assert store.apps() == [_org_app()]
        assert all(ORG_APP_SECRET not in str(app) for app in store.apps() if app.get(OAUTH_APP_OWNER_FIELD))

    async def test_the_full_save_makes_a_personal_app_only_from_what_was_sent(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "clientId": "org-client", "connectorScope": "personal"}},
        })

        await _save_full(store, ALICE, {"clientId": "alice-client", "clientSecret": ALICE_SECRET})

        org_app, own_app = store.apps()
        assert org_app == _org_app()
        assert own_app[OAUTH_APP_OWNER_FIELD] == ALICE
        assert own_app["config"] == {"clientId": "alice-client", "clientSecret": ALICE_SECRET}
        assert _linked_app_id(store) == own_app["_id"]

    async def test_a_member_s_auth_save_does_not_change_the_org_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ALICE, {"oauthConfigId": "org-app", "clientId": "alice-client", "clientSecret": ALICE_SECRET})

        org_app, own_app = store.apps()
        assert org_app == _org_app()
        assert own_app[OAUTH_APP_OWNER_FIELD] == ALICE
        assert _linked_app_id(store) == own_app["_id"]


@pytest.mark.asyncio
class TestTheOwnerChangesTheirApp:
    async def test_the_owner_s_new_secret_updates_their_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ALICE, {"oauthConfigId": "alice-app", "clientSecret": "rotated"})

        [app] = store.apps()
        assert app["config"]["clientSecret"] == "rotated"
        assert app[OAUTH_APP_OWNER_FIELD] == ALICE

    async def test_an_admin_can_not_change_or_link_a_member_s_app_from_a_connector(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        # Named like a missing app: the admin's values go into a new org app of their own.
        await _save_auth(
            store, ADMIN, {"oauthConfigId": "alice-app", "clientId": "admin-client", "clientSecret": "admin-value"},
            admin=True, scope="team",
        )

        alice_app, admin_app = store.apps()
        assert alice_app == _personal_app(ALICE, "alice-app")
        assert OAUTH_APP_OWNER_FIELD not in admin_app
        assert _linked_app_id(store) == admin_app["_id"]

    async def test_the_admin_app_route_can_not_find_a_member_s_app(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})
        request = _request(ADMIN, admin=True, store=store, body={"config": {"clientSecret": "admin-value"}})

        with pytest.raises(HTTPException) as exc:
            await update_oauth_config("Drive", "alice-app", request, config_service=store)

        assert exc.value.status_code == 404
        assert store.apps()[0] == _personal_app(ALICE, "alice-app")


@pytest.mark.asyncio
class TestWhoSeesWhichApps:
    def _store(self) -> InMemoryConfigService:
        return InMemoryConfigService({
            APPS_PATH: [_org_app(), _personal_app(ALICE, "alice-app"), _personal_app(BOB, "bob-app", secret="bob-secret")],
        })

    async def _list(self, store: InMemoryConfigService, user_id: str, *, admin: bool = False, scope: str | None = None) -> dict[str, Any]:
        request = _request(user_id, admin=admin, store=store)
        with patch.object(router_module, "can_reveal_secrets", return_value=True):
            return await list_oauth_configs("Drive", request, page=1, limit=20, search=None, scope=scope, config_service=store)

    async def test_a_member_sees_the_org_s_apps_and_their_own_by_name(self) -> None:
        result = await self._list(self._store(), ALICE)

        rows = {row["_id"]: row for row in result["oauthConfigs"]}
        assert set(rows) == {"org-app", "alice-app"}
        assert rows["alice-app"]["personal"] is True
        assert "config" not in rows["alice-app"]
        assert result["pagination"]["totalCount"] == 2

    async def test_an_admin_sees_only_the_org_s_apps(self) -> None:
        result = await self._list(self._store(), ADMIN, admin=True)

        assert [row["_id"] for row in result["oauthConfigs"]] == ["org-app"]
        assert ALICE_SECRET not in str(result) and "bob-secret" not in str(result)

    async def test_a_team_connector_s_picker_lists_no_personal_apps(self) -> None:
        result = await self._list(self._store(), ALICE, scope="team")

        assert [row["_id"] for row in result["oauthConfigs"]] == ["org-app"]

    async def test_reading_a_teammate_s_app_answers_not_found(self) -> None:
        store = self._store()
        for user_id, admin in ((BOB, False), (ADMIN, True)):
            request = _request(user_id, admin=admin, store=store)
            with pytest.raises(HTTPException) as exc, patch.object(router_module, "can_reveal_secrets", return_value=True):
                await get_oauth_config_by_id("Drive", "alice-app", request, config_service=store)
            assert exc.value.status_code == 404

    async def test_the_owner_reads_their_app_without_its_secret(self) -> None:
        store = self._store()
        request = _request(ALICE, store=store)
        with patch.object(router_module, "can_reveal_secrets", return_value=True):
            result = await get_oauth_config_by_id("Drive", "alice-app", request, config_service=store)

        assert result["oauthConfig"]["personal"] is True
        assert ALICE_SECRET not in str(result)

    async def test_the_admin_catalog_leaves_people_s_apps_out(self) -> None:
        store = self._store()
        request = _request(ADMIN, admin=True, store=store)
        with patch("app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry") as registry:
            registry.return_value.get_oauth_connectors.return_value = ["Drive"]
            result = await get_all_oauth_configs(request, page=1, limit=20, search=None, config_service=store)

        assert [row["_id"] for row in result["oauthConfigs"]] == ["org-app"]


@pytest.mark.asyncio
class TestSigningIn:
    async def _flow(self, store: InMemoryConfigService, user_id: str, connector: dict[str, Any] | None) -> dict[str, Any]:
        return await _build_oauth_flow_config(
            auth_config={"oauthConfigId": "alice-app", "connectorScope": "personal"},
            connector_type="Drive",
            org_id=ORG_ID,
            config_service=store,
            logger=logging.getLogger("test_personal_oauth_apps"),
            registry_type="Drive",
            user_id=user_id,
            connector=connector,
        )

    async def test_the_owner_signs_in_with_their_app(self) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        flow = await self._flow(store, ALICE, _instance(ALICE))

        assert flow["clientSecret"] == ALICE_SECRET

    @pytest.mark.parametrize(
        ("user_id", "connector"),
        [
            (BOB, _instance(BOB)),
            (ADMIN, _instance(ALICE)),
            (ALICE, _instance(ALICE, scope="team")),
            (ALICE, None),
        ],
    )
    async def test_no_one_else_signs_in_with_it(self, user_id: str, connector: dict[str, Any] | None) -> None:
        store = InMemoryConfigService({APPS_PATH: [_personal_app(ALICE, "alice-app")]})

        with pytest.raises(HTTPException) as exc:
            await self._flow(store, user_id, connector)

        assert exc.value.status_code == 404


@pytest.mark.asyncio
class TestDeletedUser:
    async def test_a_deleted_user_s_apps_go_and_nothing_else(self) -> None:
        other_org_app = {**_personal_app(ALICE, "elsewhere"), "orgId": "org-2"}
        store = InMemoryConfigService({
            APPS_PATH: [_org_app(), _personal_app(ALICE, "alice-app"), _personal_app(BOB, "bob-app"), other_org_app],
            "/services/oauth/gmail": [{**_personal_app(ALICE, "alice-gmail"), "connectorType": "Gmail"}],
        })

        removed = await remove_personal_oauth_apps(store, ORG_ID, ALICE)

        assert removed == 2
        assert [app["_id"] for app in store.apps()] == ["org-app", "bob-app", "elsewhere"]
        assert store.values["/services/oauth/gmail"] == []


@pytest.mark.asyncio
class TestPublicOnly:
    @pytest.mark.parametrize(
        ("auth", "public_only"),
        [
            ({"connectorScope": "team", "oauthConfigId": "alice-app", "connectorType": "Drive"}, False),
            ({"connectorScope": "personal", "oauthConfigId": "org-app", "connectorType": "Drive"}, False),
            ({"connectorScope": "personal", "oauthConfigId": "alice-app", "connectorType": "Drive"}, True),
            # An app that is gone, or no app at all, can't vouch for the address.
            ({"connectorScope": "personal", "oauthConfigId": "deleted-app", "connectorType": "Drive"}, True),
            ({"connectorScope": "personal", "connectorType": "Drive"}, True),
        ],
    )
    async def test_only_an_org_app_lets_a_personal_connector_reach_private_addresses(
        self, auth: dict[str, Any], public_only: bool
    ) -> None:
        store = InMemoryConfigService({APPS_PATH: [_org_app(), _personal_app(ALICE, "alice-app")]})

        assert await oauth_app_public_only(auth, store) is public_only


class _ConnectorConfigWriteFails(InMemoryConfigService):
    """The OAuth app list saves; the connector's own config does not."""

    async def set_config(self, path: str, value: object) -> bool:
        if path == CONFIG_PATH:
            return False
        return await super().set_config(path, value)


@pytest.mark.asyncio
class TestAnAdminsPersonalConnector:
    async def test_a_whole_client_makes_the_admin_s_own_app_and_leaves_the_org_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ADMIN, {"oauthConfigId": "org-app", "clientId": "admin-client", "clientSecret": "admin-secret"}, admin=True)

        org_app, own_app = store.apps()
        assert org_app == _org_app()
        assert own_app[OAUTH_APP_OWNER_FIELD] == ADMIN
        assert _linked_app_id(store) == own_app["_id"]

    async def test_part_of_a_client_changes_no_org_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ADMIN, {"oauthConfigId": "org-app", "clientSecret": "admin-secret"}, admin=True)

        assert store.apps() == [_org_app()]
        assert _linked_app_id(store) == "org-app"

    async def test_the_full_save_changes_no_org_app_either(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_org_app()],
            CONFIG_PATH: {"auth": {"oauthConfigId": "org-app", "clientId": "org-client", "connectorScope": "personal"}},
        })

        await _save_full(store, ADMIN, {"oauthConfigId": "org-app", "clientSecret": "admin-secret"}, admin=True)

        assert store.apps() == [_org_app()]
        assert _linked_app_id(store) == "org-app"


@pytest.mark.asyncio
class TestTheConnectorScopeIsTheServers:
    async def test_an_auth_save_can_not_make_a_personal_connector_look_like_a_team_one(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ALICE, {"oauthConfigId": "alice-app", "connectorScope": "team"})

        assert store.values[CONFIG_PATH]["auth"]["connectorScope"] == "personal"

    async def test_a_stored_wrong_scope_is_put_right_on_the_next_save(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "team"}},
        })

        await _save_auth(store, ALICE, {"oauthConfigId": "alice-app"})

        assert store.values[CONFIG_PATH]["auth"]["connectorScope"] == "personal"

    async def test_the_full_save_can_not_change_it_either(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "personal"}},
        })

        await _save_full(store, ALICE, {"oauthConfigId": "alice-app", "connectorScope": "team"})

        assert store.values[CONFIG_PATH]["auth"]["connectorScope"] == "personal"


@pytest.mark.asyncio
class TestAFailedSaveLeavesNoApp:
    async def test_an_auth_save_whose_connector_config_is_not_stored_takes_its_new_app_back(self) -> None:
        store = _ConnectorConfigWriteFails({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        with pytest.raises(HTTPException) as exc:
            await _save_auth(store, ALICE, {"clientId": "alice-client", "clientSecret": ALICE_SECRET})

        assert exc.value.status_code == 500
        assert store.apps() == []

    async def test_an_auth_save_whose_connector_config_is_not_stored_puts_the_app_back(self) -> None:
        store = _ConnectorConfigWriteFails({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "personal"}},
        })

        with pytest.raises(HTTPException):
            await _save_auth(store, ALICE, {"oauthConfigId": "alice-app", "clientSecret": "rotated"})

        assert store.apps() == [_personal_app(ALICE, "alice-app")]

    async def test_a_connector_that_could_not_be_created_takes_its_new_app_back(self) -> None:
        store = InMemoryConfigService()
        request = _request(ALICE, store=store, body={
            "connectorType": "Drive", "instanceName": "My Drive", "scope": "personal", "authType": "OAUTH",
            "config": {"auth": {"clientId": "alice-client", "clientSecret": ALICE_SECRET}},
        })
        request.app.state.connector_registry.create_connector_instance_on_configuration = AsyncMock(return_value=None)
        graph = AsyncMock()
        graph.get_account_type = AsyncMock(return_value="individual")

        with (
            patch.object(router_module, "resolve_config_service", return_value=store),
            patch.object(router_module, "check_beta_connector_access", AsyncMock()),
            patch.object(router_module, "_store_config_action_items", AsyncMock()),
            pytest.raises(HTTPException) as exc,
        ):
            await create_connector_instance(request, graph)

        assert exc.value.status_code == 500
        assert store.apps() == []

    async def test_an_app_that_could_not_be_saved_leaves_no_connector(self) -> None:
        store = InMemoryConfigService()
        request = _request(ALICE, store=store, body={
            "connectorType": "Drive", "instanceName": "My Drive", "scope": "personal", "authType": "OAUTH",
            "config": {"auth": {"clientId": "alice-client", "clientSecret": ALICE_SECRET}},
        })
        graph = AsyncMock()
        graph.get_account_type = AsyncMock(return_value="individual")

        with (
            patch.object(router_module, "resolve_config_service", return_value=store),
            patch.object(router_module, "check_beta_connector_access", AsyncMock()),
            patch.object(router_module, "_create_or_update_oauth_config", AsyncMock(return_value=None)),
            pytest.raises(HTTPException),
        ):
            await create_connector_instance(request, graph)

        request.app.state.connector_registry.create_connector_instance_on_configuration.assert_not_called()


@pytest.mark.asyncio
class TestTheAppWriter:
    async def test_a_new_name_is_kept_when_the_owner_updates_their_app(self) -> None:
        store = InMemoryConfigService({
            APPS_PATH: [_personal_app(ALICE, "alice-app")],
            CONFIG_PATH: {"auth": {"oauthConfigId": "alice-app", "connectorScope": "personal"}},
        })

        await _save_auth(store, ALICE, {"oauthConfigId": "alice-app", "clientSecret": "rotated", "oauthInstanceName": "Renamed"})

        [app] = store.apps()
        assert app["oauthInstanceName"] == "Renamed"
        assert app["config"]["clientSecret"] == "rotated"

    async def test_part_of_a_client_for_an_app_gone_since_the_plan_creates_nothing(self) -> None:
        store = InMemoryConfigService()

        written = await router_module._create_or_update_oauth_config(
            connector_type="Drive",
            auth_config={"clientSecret": "rotated"},
            instance_name="My app",
            user_id=ALICE,
            org_id=ORG_ID,
            is_admin=False,
            config_service=store,
            base_url="",
            oauth_app_id="alice-app",
            connector_scope="personal",
            owner_user_id=ALICE,
        )

        assert written is None
        assert store.apps() == []

    async def test_a_whole_client_for_an_app_gone_since_the_plan_is_still_its_owners(self) -> None:
        store = InMemoryConfigService()

        written = await router_module._create_or_update_oauth_config(
            connector_type="Drive",
            auth_config={"clientId": "alice-client", "clientSecret": ALICE_SECRET},
            instance_name="My app",
            user_id=ALICE,
            org_id=ORG_ID,
            is_admin=False,
            config_service=store,
            base_url="",
            oauth_app_id="alice-app",
            connector_scope="personal",
            owner_user_id=ALICE,
        )

        [app] = store.apps()
        assert written == app["_id"]
        assert app[OAUTH_APP_OWNER_FIELD] == ALICE


@pytest.mark.asyncio
class TestAFailedRemovalIsRetried:
    async def test_the_user_deleted_event_fails_so_it_comes_again(self) -> None:
        from app.services.messaging.kafka.handlers.entity import EntityEventService

        service = EntityEventService(logging.getLogger("test_personal_oauth_apps"), AsyncMock(), MagicMock())
        with (
            patch("app.connectors.api.connector_resolvers.resolve_config_service", return_value=MagicMock()),
            patch("app.utils.oauth_config.remove_personal_oauth_apps", AsyncMock(side_effect=RuntimeError("store down"))),
            pytest.raises(RuntimeError),
        ):
            await service._remove_personal_oauth_apps(ORG_ID, ALICE)
