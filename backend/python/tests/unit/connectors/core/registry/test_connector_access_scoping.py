"""Tenant and role scoping on connector access.

Connector instances are fetched by id alone (`_get_connector_instance_from_db`
issues a plain `get_document`), so every isolation guarantee lives in
`_can_access_connector`.  The tenant check uses the instance's ``orgId``
field — without it a TEAM connector's gate reduces to `is_admin`, and an
administrator of one organization who learns an id belonging to another
would pass it.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.connector_registry import ConnectorRegistry

pytestmark = pytest.mark.asyncio

ORG = "org-acme"
OTHER_ORG = "org-globex"
CONN_ID = "conn-1"


def _registry() -> ConnectorRegistry:
    registry = ConnectorRegistry.__new__(ConnectorRegistry)
    registry.logger = MagicMock()
    return registry


def _instance(*, scope: str, created_by: str = "user-a", org_id: str = ORG) -> dict:
    return {"_key": CONN_ID, "scope": scope, "createdBy": created_by, "orgId": org_id}


class TestTenantIsolation:
    async def test_admin_cannot_reach_another_orgs_team_connector(self):
        """The gap this closes: for TEAM scope the role check alone would pass,
        and instances are looked up by id with no org filter."""
        allowed = await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, org_id=OTHER_ORG),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )
        assert allowed is False

    async def test_creator_cannot_reach_their_connector_from_another_org_context(self):
        allowed = await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, org_id=OTHER_ORG),
            "user-a",
            ORG,
            is_admin=False,
        )
        assert allowed is False

    async def test_matching_org_still_allows_admin(self):
        allowed = await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )
        assert allowed is True

    async def test_the_mismatch_is_logged(self):
        registry = _registry()
        await registry._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, org_id=OTHER_ORG),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )
        registry.logger.warning.assert_called()


def _registry_with_org_edges(edges: list[dict]) -> tuple[ConnectorRegistry, AsyncMock]:
    registry = _registry()
    graph_provider = AsyncMock()
    graph_provider.get_edges_to_node = AsyncMock(return_value=edges)
    registry._graph_provider = graph_provider
    return registry, graph_provider


def _legacy_instance(*, scope: str, created_by: str = "user-a") -> dict:
    """Shaped like an instance created before ``orgId`` was stamped on apps."""
    return {"_key": CONN_ID, "scope": scope, "createdBy": created_by}


class TestLegacyInstancesWithoutOrgId:
    """Without ``orgId`` the org edge decides, rather than letting the instance through."""

    async def test_admin_cannot_reach_another_orgs_legacy_team_connector(self) -> None:
        registry, graph_provider = _registry_with_org_edges(
            [{"_from": f"organizations/{OTHER_ORG}", "_to": f"apps/{CONN_ID}"}]
        )

        allowed = await registry._can_access_connector(
            _legacy_instance(scope=ConnectorScope.TEAM.value),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )

        assert allowed is False
        graph_provider.get_edges_to_node.assert_awaited_once_with(
            f"apps/{CONN_ID}", "orgAppRelation"
        )

    async def test_admin_of_another_org_cannot_delete_a_legacy_connector(self) -> None:
        registry, _ = _registry_with_org_edges([{"from_id": OTHER_ORG, "to_id": CONN_ID}])

        allowed = await registry._can_delete_connector(
            _legacy_instance(scope=ConnectorScope.TEAM.value),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )

        assert allowed is False

    async def test_a_legacy_connector_with_no_org_edge_is_refused(self) -> None:
        registry, _ = _registry_with_org_edges([])

        allowed = await registry._can_access_connector(
            _legacy_instance(scope=ConnectorScope.TEAM.value),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )

        assert allowed is False

    async def test_own_orgs_legacy_connector_is_still_reachable(self) -> None:
        """Neo4j edges carry a bare id, Arango edges a document handle; both count."""
        for edge in ({"from_id": ORG}, {"_from": f"organizations/{ORG}"}):
            registry, _ = _registry_with_org_edges([edge])

            allowed = await registry._can_access_connector(
                _legacy_instance(scope=ConnectorScope.TEAM.value),
                "admin-of-acme",
                ORG,
                is_admin=True,
            )

            assert allowed is True

    async def test_an_instance_with_org_id_needs_no_edge_lookup(self) -> None:
        registry, graph_provider = _registry_with_org_edges([])

        assert await registry._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value), "admin-of-acme", ORG, is_admin=True
        )
        graph_provider.get_edges_to_node.assert_not_awaited()


class TestRoleScopingUnchanged:
    """The tenant check is added in front of these, not instead of them."""

    async def test_team_admin_allowed(self):
        assert await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="someone-else"),
            "admin",
            ORG,
            is_admin=True,
        )

    async def test_team_creator_allowed(self):
        assert await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

    async def test_team_stranger_denied(self):
        assert not await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-b",
            ORG,
            is_admin=False,
        )

    async def test_personal_creator_allowed(self):
        assert await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

    async def test_personal_admin_denied(self):
        """A personal connector holds one user's own credentials; admin
        authority stops at team scope."""
        assert not await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "admin",
            ORG,
            is_admin=True,
        )

    async def test_unknown_scope_denied(self):
        assert not await _registry()._can_access_connector(
            _instance(scope="SOMETHING_ELSE"),
            "user-a",
            ORG,
            is_admin=True,
        )


class TestDeletionGate:
    """`_can_delete_connector` is deliberately more permissive than
    `_can_access_connector`, and only along the creator/admin axis.

    Routing deletion through the read gate 404s an administrator on another
    user's personal connector, which made the admin allowance in
    `_validate_connector_deletion_permissions` unreachable for precisely the
    orphaned instances it exists to clean up.
    """

    async def test_admin_may_delete_another_users_personal_connector(self):
        """The case the read gate refuses; the whole reason this gate exists."""
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "admin-b",
            ORG,
            is_admin=True,
        )

        assert allowed is True

    async def test_read_access_to_that_same_connector_is_still_refused(self):
        """Pins the asymmetry: deleting a connector is not seeing its data, and
        widening the read gate instead would have granted both."""
        allowed = await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "admin-b",
            ORG,
            is_admin=True,
        )

        assert allowed is False

    async def test_creator_may_delete_their_own_personal_connector(self):
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

        assert allowed is True

    async def test_creator_may_delete_their_own_team_connector(self):
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

        assert allowed is True

    async def test_a_non_admin_stranger_may_not_delete(self):
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-c",
            ORG,
            is_admin=False,
        )

        assert allowed is False

    async def test_admin_of_another_org_may_not_delete(self):
        """Tenant isolation is checked before the role, so the wider deletion
        allowance never becomes a cross-tenant one."""
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "admin-b",
            OTHER_ORG,
            is_admin=True,
        )

        assert allowed is False

    async def test_creator_in_another_org_may_not_delete(self):
        """A createdBy match must not defeat the tenant check either."""
        allowed = await _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            OTHER_ORG,
            is_admin=False,
        )

        assert allowed is False


class TestCanUserViewConnector:
    """Stats visibility: only admin or creator, never arbitrary team members."""

    async def test_personal_creator_can_view(self):
        assert await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            is_admin=False,
        )

    async def test_personal_non_creator_denied(self):
        assert not await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-b",
            is_admin=False,
        )

    async def test_personal_admin_non_creator_denied(self):
        assert not await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "admin",
            is_admin=True,
        )

    async def test_team_admin_can_view(self):
        assert await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "admin",
            is_admin=True,
        )

    async def test_team_creator_can_view(self):
        assert await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-a",
            is_admin=False,
        )

    async def test_team_member_denied(self):
        """A non-admin, non-creator org member must not see team connector stats."""
        assert not await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-b",
            is_admin=False,
        )

    async def test_unknown_scope_denied(self):
        assert not await _registry().can_user_view_connector(
            CONN_ID,
            _instance(scope="SOMETHING_ELSE"),
            "user-a",
            is_admin=True,
        )
