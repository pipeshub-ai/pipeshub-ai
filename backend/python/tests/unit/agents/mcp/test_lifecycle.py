"""MCP data goes away with what it belongs to (`app.agents.mcp.lifecycle`)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp import lifecycle
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService


def _instance(instance_id: str, *, org: str = "org-1", owner: str = "admin-1", scope: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "_id": instance_id, "orgId": org, "createdBy": owner, "name": instance_id,
        "transport": "streamable_http", "authMode": "oauth", "isCustom": True, "createdAt": 1, "updatedAt": 1,
    }
    if scope:
        record["scope"] = scope
    return record


def _cred(org: str = "org-1") -> dict[str, Any]:
    return {"isAuthenticated": True, "orgId": org, "oauthTokens": {"accessToken": "a", "refreshToken": "r"}}


def _store() -> FakeConfigService:
    return FakeConfigService({
        # org-1: an org server and two personal ones
        "/services/mcp/instances/gh": _instance("gh"),
        "/services/mcp/user-instances/org-1/u-alice/alice-own": _instance("alice-own", owner="u-alice", scope="personal"),
        "/services/mcp/user-instances/org-1/u-bob/bob-own": _instance("bob-own", owner="u-bob", scope="personal"),
        "/services/mcp/credentials/gh/u-alice": _cred(),
        "/services/mcp/credentials/gh/u-alice/dcr-client": {"clientId": "c"},
        "/services/mcp/credentials/gh/u-bob": _cred(),
        "/services/mcp/credentials/gh/_shared": {"isAuthenticated": True},
        "/services/mcp/credentials/gh/agent-1": _cred(),
        "/services/mcp/credentials/alice-own/u-alice": _cred(),
        "/services/mcp/oauth-clients/alice-own": {"clientId": "static"},
        "/services/mcp/credentials/bob-own/u-bob": _cred(),
        # org-2: must never be touched
        "/services/mcp/instances/other": _instance("other", org="org-2"),
        "/services/mcp/credentials/other/u-alice": _cred("org-2"),
        "/services/mcp/user-instances/org-2/u-zed/zed-own": _instance("zed-own", org="org-2", owner="u-zed", scope="personal"),
    })


@pytest.fixture
def refresh_service() -> Any:  # noqa: ANN401
    service = MagicMock()
    with patch.object(lifecycle, "_refresh_service", return_value=service):
        yield service


class TestRemoveUserMcpData:
    async def test_everything_of_the_deleted_user_goes_and_nothing_else(self, refresh_service: MagicMock) -> None:
        store = _store()

        removed = await lifecycle.remove_user_mcp_data(store, "org-1", "u-alice")

        gone = {
            "/services/mcp/user-instances/org-1/u-alice/alice-own",
            "/services/mcp/credentials/alice-own/u-alice",
            "/services/mcp/oauth-clients/alice-own",
            "/services/mcp/credentials/gh/u-alice",
            "/services/mcp/credentials/gh/u-alice/dcr-client",
        }
        assert gone <= set(removed)
        assert not gone & store.data.keys()
        for kept in (
            "/services/mcp/credentials/gh/u-bob", "/services/mcp/credentials/gh/_shared",
            "/services/mcp/credentials/gh/agent-1", "/services/mcp/credentials/bob-own/u-bob",
            # Same user id in another org: not this org's data.
            "/services/mcp/credentials/other/u-alice",
        ):
            assert kept in store.data
        refresh_service.cancel_refresh_task.assert_any_call("/services/mcp/credentials/gh/u-alice")
        refresh_service.cancel_refresh_tasks_for_instance.assert_any_call("alice-own")


class TestRemoveOwnerCredentials:
    async def test_a_deleted_agent_loses_only_its_own_credentials(self, refresh_service: MagicMock) -> None:
        store = _store()

        removed = await lifecycle.remove_owner_credentials(store, "org-1", "agent-1")

        assert removed == ["/services/mcp/credentials/gh/agent-1"]
        assert "/services/mcp/credentials/gh/u-bob" in store.data
        refresh_service.cancel_refresh_task.assert_called_once_with("/services/mcp/credentials/gh/agent-1")


class TestRemoveOrgMcpData:
    async def test_every_server_of_the_org_goes_and_other_orgs_stay(self, refresh_service: MagicMock) -> None:
        store = _store()

        await lifecycle.remove_org_mcp_data(store, "org-1")

        assert not [k for k in store.data if "/gh" in k or "alice-own" in k or "bob-own" in k]
        assert "/services/mcp/instances/other" in store.data
        assert "/services/mcp/credentials/other/u-alice" in store.data
        assert "/services/mcp/user-instances/org-2/u-zed/zed-own" in store.data


class TestBestEffort:
    async def test_one_failing_delete_does_not_stop_the_rest(self, refresh_service: MagicMock) -> None:
        store = _store()
        real_delete = store.delete_config

        async def _delete(key: str) -> bool:
            if key.endswith("/gh/u-alice"):
                raise RuntimeError("store hiccup")
            return await real_delete(key)

        store.delete_config = _delete  # type: ignore[method-assign]
        removed = await lifecycle.remove_user_mcp_data(store, "org-1", "u-alice")

        assert "/services/mcp/credentials/gh/u-alice" not in removed
        assert "/services/mcp/credentials/gh/u-alice/dcr-client" in removed
        assert "/services/mcp/user-instances/org-1/u-alice/alice-own" not in store.data


class TestCredentialHasInstance:
    async def test_an_org_or_personal_server_that_exists(self) -> None:
        store = _store()
        assert await lifecycle.credential_has_instance(store, "/services/mcp/credentials/gh/u-bob") is True
        assert await lifecycle.credential_has_instance(store, "/services/mcp/credentials/bob-own/u-bob") is True

    async def test_a_server_that_is_gone(self) -> None:
        store = _store()
        store.data["/services/mcp/credentials/deleted-inst/u-bob"] = _cred()
        assert await lifecycle.credential_has_instance(store, "/services/mcp/credentials/deleted-inst/u-bob") is False

    async def test_unknown_when_the_store_fails_or_the_record_names_no_org(self) -> None:
        store = _store()
        store.data["/services/mcp/credentials/deleted-inst/u-bob"] = {"isAuthenticated": True}
        assert await lifecycle.credential_has_instance(store, "/services/mcp/credentials/deleted-inst/u-bob") is None

        failing = MagicMock()
        failing.get_config = AsyncMock(side_effect=RuntimeError("store down"))
        assert await lifecycle.credential_has_instance(failing, "/services/mcp/credentials/gh/u-bob") is None

    async def test_a_path_that_is_not_a_credential_record(self) -> None:
        assert await lifecycle.credential_has_instance(_store(), "/services/mcp/credentials/gh/u-alice/dcr-client") is None


class TestDeletionEventsCleanUp:
    def _service(self, store: FakeConfigService) -> Any:  # noqa: ANN401
        from app.services.messaging.kafka.handlers.entity import EntityEventService

        graph = MagicMock()
        graph.get_entity_id_by_email = AsyncMock(return_value="user-key")
        graph.batch_upsert_nodes = AsyncMock()
        container = MagicMock()
        container.config_service.return_value = store
        return EntityEventService(MagicMock(), graph, container)

    async def test_user_deleted_removes_their_mcp_data(self, refresh_service: MagicMock) -> None:
        store = _store()

        ok = await self._service(store).process_event(
            "userDeleted", {"orgId": "org-1", "userId": "u-alice", "email": "alice@example.com"},
        )

        assert ok is True
        assert "/services/mcp/credentials/gh/u-alice" not in store.data
        assert "/services/mcp/user-instances/org-1/u-alice/alice-own" not in store.data

    async def test_org_deleted_removes_its_mcp_data(self, refresh_service: MagicMock) -> None:
        store = _store()

        ok = await self._service(store).process_event("orgDeleted", {"orgId": "org-1"})

        assert ok is True
        assert "/services/mcp/instances/gh" not in store.data
        assert "/services/mcp/instances/other" in store.data

    async def test_a_failing_cleanup_does_not_fail_the_event(self) -> None:
        service = self._service(_store())
        with patch.object(lifecycle, "remove_user_mcp_data", new=AsyncMock(side_effect=RuntimeError("down"))):
            ok = await service.process_event(
                "userDeleted", {"orgId": "org-1", "userId": "u-alice", "email": "alice@example.com"},
            )

        assert ok is True


class TestRefreshPassSkipsOrphans:
    async def test_a_credential_whose_server_is_gone_is_removed_not_refreshed(self) -> None:
        from app.connectors.core.base.token_service.mcp_token_refresh_service import (
            MCPTokenRefreshService,
        )

        store = _store()
        store.data["/services/mcp/credentials/deleted-inst/u-bob"] = _cred()
        service = MCPTokenRefreshService(store)
        refreshed: list[str] = []

        async def _record(path: str) -> None:
            refreshed.append(path)

        with patch.object(service, "_refresh_credential", new=_record):
            await service._refresh_all_tokens_internal()

        assert "/services/mcp/credentials/deleted-inst/u-bob" not in store.data
        assert "/services/mcp/credentials/deleted-inst/u-bob" not in refreshed
        assert "/services/mcp/credentials/gh/u-bob" in refreshed
