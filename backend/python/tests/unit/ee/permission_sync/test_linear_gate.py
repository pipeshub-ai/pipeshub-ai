"""Without a licence key a company-wide Linear connector reads no access through these methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ee.permission_sync.linear import LinearPermissionsMixin
from app.models.permission import EntityType


def _connector(*, gated: bool) -> LinearPermissionsMixin:
    """The mixin alone, with the Linear API and the processor replaced by mocks."""
    connector = type("Connector", (LinearPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector._api = MagicMock()
    connector._api.complete = AsyncMock()
    connector._api.pages = MagicMock(side_effect=AssertionError("Linear was asked for parent teams"))
    return connector


PUBLIC = {"id": "t-1", "visibility": "public"}
RESTRICTED = {"id": "t-2", "visibility": "restricted", "parent": {"id": "t-hidden"}}


@pytest.mark.asyncio
async def test_gated_connector_reads_no_team_members() -> None:
    connector = _connector(gated=True)

    await connector._read_team_members(PUBLIC)

    connector._api.complete.assert_not_awaited()
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")


@pytest.mark.asyncio
async def test_gated_team_grants_cover_nobody() -> None:
    # Empty, not None: the team is still written as a record group, and the processor keeps its stored grants.
    assert await _connector(gated=True)._team_grants(PUBLIC, {"t-1": PUBLIC}) == []


@pytest.mark.asyncio
async def test_gated_connector_fetches_no_parent_teams() -> None:
    teams = {"t-2": RESTRICTED}

    await _connector(gated=True)._add_missing_ancestor_teams(teams)

    assert teams == {"t-2": RESTRICTED}


@pytest.mark.asyncio
async def test_licensed_connector_still_reads_members_and_grants() -> None:
    connector = _connector(gated=False)

    await connector._read_team_members(PUBLIC)
    grants = await connector._team_grants(PUBLIC, {"t-1": PUBLIC})

    connector._api.complete.assert_awaited_once()
    assert {(g.entity_type, g.external_id) for g in grants} == {
        (EntityType.GROUP, "t-1"), (EntityType.GROUP, "linear-workspace-members"),
    }
