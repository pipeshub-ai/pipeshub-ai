"""Drive streams a file shared only through a Google group as one of the group's members.

The candidates for impersonation were the users granted on the file or above it;
a file shared only with a group had none and fell to the service account, which
cannot open a file in someone's My Drive.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.google.common.impersonation import (
    MAX_IMPERSONATION_CANDIDATES,
)
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector


@pytest.fixture
def connector() -> GoogleDriveTeamConnector:
    with patch("app.connectors.sources.google.drive.team.connector.GoogleDriveTeamApp"):
        conn = GoogleDriveTeamConnector(
            logger=logging.getLogger("drive-group-candidates"),
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=AsyncMock(),
            connector_id="drive-1",
            scope="team",
            created_by="admin",
        )
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    return conn


def _group(key: str) -> MagicMock:
    return MagicMock(source_user_group_id=key)


class TestGroupMemberCandidates:
    @pytest.mark.asyncio
    async def test_the_members_of_every_granted_group_are_candidates_once(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(
            return_value=[_group("eng@x.com"), _group("ops@x.com")]
        )
        members = {
            "eng@x.com": [{"email": "Ana@x.com", "type": "USER"}, {"email": "nested@x.com", "type": "GROUP"}],
            "ops@x.com": [{"email": "ana@x.com", "type": "USER"}, {"email": "bo@x.com", "type": "USER"}],
        }
        connector._fetch_group_members = AsyncMock(side_effect=lambda key, include_derived: members[key])

        candidates = await connector._group_member_candidates(MagicMock(id="rec-1"))

        assert [c.email for c in candidates] == ["ana@x.com", "bo@x.com"]
        connector._fetch_group_members.assert_any_await("eng@x.com", include_derived=True)

    @pytest.mark.asyncio
    async def test_the_list_is_capped(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(return_value=[_group("big@x.com")])
        connector._fetch_group_members = AsyncMock(
            return_value=[{"email": f"u{i}@x.com", "type": "USER"} for i in range(MAX_IMPERSONATION_CANDIDATES + 10)]
        )

        assert len(await connector._group_member_candidates(MagicMock(id="rec-1"))) == MAX_IMPERSONATION_CANDIDATES

    @pytest.mark.asyncio
    async def test_a_group_that_cannot_be_listed_is_skipped(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(
            return_value=[_group("broken@x.com"), _group("ok@x.com")]
        )

        async def members(key: str, include_derived: bool) -> list[dict]:
            if key == "broken@x.com":
                raise RuntimeError("403")
            return [{"email": "bo@x.com", "type": "USER"}]

        connector._fetch_group_members = AsyncMock(side_effect=members)

        assert [c.email for c in await connector._group_member_candidates(MagicMock(id="rec-1"))] == ["bo@x.com"]

    @pytest.mark.asyncio
    async def test_unreadable_grants_give_no_candidate(self, connector) -> None:
        connector.data_entities_processor.get_groups_with_access_to_record = AsyncMock(side_effect=RuntimeError("db down"))

        assert await connector._group_member_candidates(MagicMock(id="rec-1")) == []
