"""Tenant and role scoping on connector access.

Connector instances are fetched by id alone (`_get_connector_instance_from_db`
issues a plain `get_document`), so every isolation guarantee lives in
`_can_access_connector`. Without an org comparison there, a TEAM connector's
gate reduces to `is_admin` — and an administrator is an administrator of *an*
organization, not of all of them.
"""

from unittest.mock import MagicMock

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.connector_registry import ConnectorRegistry

pytestmark = pytest.mark.asyncio

ORG = "org-acme"
OTHER_ORG = "org-globex"


def _registry() -> ConnectorRegistry:
    registry = ConnectorRegistry.__new__(ConnectorRegistry)
    registry.logger = MagicMock()
    return registry


def _instance(*, scope: str, created_by: str = "user-a", org_id: str | None = ORG) -> dict:
    doc = {"_key": "conn-1", "scope": scope, "createdBy": created_by}
    if org_id is not None:
        doc["orgId"] = org_id
    return doc


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
            _instance(scope=ConnectorScope.TEAM.value, org_id=ORG),
            "admin-of-acme",
            ORG,
            is_admin=True,
        )
        assert allowed is True

    async def test_an_instance_without_an_org_is_not_blocked(self):
        """Legacy documents predating the field must stay reachable; the role
        checks below still apply to them."""
        allowed = await _registry()._can_access_connector(
            _instance(scope=ConnectorScope.TEAM.value, org_id=None),
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
        allowed = _registry()._can_delete_connector(
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
        allowed = _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

        assert allowed is True

    async def test_creator_may_delete_their_own_team_connector(self):
        allowed = _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-a",
            ORG,
            is_admin=False,
        )

        assert allowed is True

    async def test_a_non_admin_stranger_may_not_delete(self):
        allowed = _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "user-c",
            ORG,
            is_admin=False,
        )

        assert allowed is False

    async def test_admin_of_another_org_may_not_delete(self):
        """Tenant isolation is checked before the role, so the wider deletion
        allowance never becomes a cross-tenant one."""
        allowed = _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.TEAM.value, created_by="user-a"),
            "admin-b",
            OTHER_ORG,
            is_admin=True,
        )

        assert allowed is False

    async def test_creator_in_another_org_may_not_delete(self):
        """A createdBy match must not defeat the tenant check either."""
        allowed = _registry()._can_delete_connector(
            _instance(scope=ConnectorScope.PERSONAL.value, created_by="user-a"),
            "user-a",
            OTHER_ORG,
            is_admin=False,
        )

        assert allowed is False
