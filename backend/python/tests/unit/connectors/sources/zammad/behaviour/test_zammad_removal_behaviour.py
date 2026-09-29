"""Zammad tickets leave the index when they leave Zammad or the sync filters.

The connector's own sync code runs for real (group listing, filters, ticket
search, attachments, sync points, removal); Zammad and our stores are the
in-memory fakes in ``zammad_behaviour_fakes``.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeRecordsDb,
    FakeStore,
    FakeZammad,
    epoch_ms,
)

from app.connectors.sources.zammad.connector import ZammadConnector

CONNECTOR_ID = "zm-1"


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammad(groups={1: "Support", 2: "Sales"})
        self.db = FakeRecordsDb()
        self.store = FakeStore()
        self.config = FakeConfigService()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.removal"),
                data_entities_processor=self.db,
                data_store_provider=self.store,
                config_service=self.config,
                connector_id=CONNECTOR_ID,
                scope="team",
                created_by="admin",
            )
        self.connector.external_client = object()
        self.connector.base_url = "https://zammad.test"
        self.connector._get_fresh_datasource = AsyncMock(return_value=self.zammad)
        self.connector._fetch_users = AsyncMock(return_value=([], {}))
        self.connector._sync_roles = AsyncMock()
        self.connector._sync_knowledge_bases = AsyncMock()

    async def sync(self) -> None:
        await self.connector.run_sync()

    async def save_filters(self, values: dict) -> None:
        """Saving filters clears the sync points and the next sync is a full one."""
        self.config.sync_filters = values
        self.store.clear()
        await self.sync()


@pytest.fixture
async def world() -> World:
    w = World()
    w.zammad.add_ticket(10, 1, day=1)
    w.zammad.add_ticket(11, 1, day=2, attachments=1)
    w.zammad.add_ticket(20, 2, day=3)
    await w.sync()
    assert w.db.external_ids() == {"10", "11", "11_1_1", "20"}
    return w


async def test_a_ticket_deleted_in_zammad_is_removed_on_the_next_incremental_sync(world: World) -> None:
    world.zammad.delete_ticket(11)

    await world.sync()

    assert world.db.external_ids() == {"10", "20"}
    # The attachment goes first, so a failure never strands it without its ticket.
    assert world.db.deleted == ["11_1_1", "11"]


async def test_a_ticket_the_search_index_has_not_caught_up_with_is_kept(world: World) -> None:
    world.zammad.unindexed.add(10)

    await world.sync()

    assert "10" in world.db.external_ids()
    assert world.db.deleted == []
    assert 10 in world.zammad.ticket_reads


async def test_a_failed_listing_removes_nothing_and_the_next_sync_catches_up(world: World) -> None:
    world.zammad.delete_ticket(11)
    world.zammad.fail_search = lambda query: "updated_at" not in query  # only the id listing fails

    await world.sync()
    assert world.db.deleted == []

    world.zammad.fail_search = lambda _query: False
    await world.sync()
    assert world.db.external_ids() == {"10", "20"}


async def test_a_ticket_zammad_cannot_read_back_is_kept(world: World) -> None:
    world.zammad.delete_ticket(11)
    world.zammad.ticket_read_status[11] = 503

    await world.sync()

    assert "11" in world.db.external_ids()
    assert world.db.deleted == []


async def test_a_failed_delete_keeps_the_ticket_so_the_next_sync_retries(world: World) -> None:
    world.zammad.delete_ticket(11)
    world.db.fail_delete_for.add("11_1_1")

    await world.sync()
    assert {"11", "11_1_1"} <= world.db.external_ids()

    world.db.fail_delete_for.clear()
    await world.sync()
    assert world.db.external_ids() == {"10", "20"}


async def test_a_group_the_filter_now_excludes_loses_its_tickets(world: World) -> None:
    await world.save_filters({"group_ids": {"operator": "not_in", "value": ["2"], "type": "list"}})

    assert world.db.external_ids() == {"10", "11", "11_1_1"}
    assert world.db.deleted == ["20"]

    reads_before = len(world.db.group_page_reads)
    await world.sync()
    # The clean-up is remembered until the filters change again.
    assert "group_2" not in world.db.group_page_reads[reads_before:]


async def test_a_failed_group_listing_removes_nothing(world: World) -> None:
    world.zammad.fail_list_groups = True

    await world.save_filters({"group_ids": {"operator": "not_in", "value": ["2"], "type": "list"}})

    assert world.db.deleted == []


async def test_a_ticket_older_than_a_new_modified_filter_is_removed(world: World) -> None:
    after = epoch_ms(2)
    await world.save_filters(
        {"modified": {"operator": "is_after", "value": {"start": after, "end": None}, "type": "datetime"}}
    )

    assert "10" not in world.db.external_ids()
    assert {"11", "11_1_1", "20"} <= world.db.external_ids()


async def test_a_ticket_moved_into_an_excluded_group_is_removed(world: World) -> None:
    await world.save_filters({"group_ids": {"operator": "in", "value": ["1"], "type": "list"}})
    world.zammad.move_ticket(10, 2, day=5)

    await world.sync()

    assert "10" not in world.db.external_ids()
    assert {"11", "11_1_1"} <= world.db.external_ids()


async def test_sync_points_hold_only_values_neo4j_can_store(world: World) -> None:
    await world.save_filters({"group_ids": {"operator": "not_in", "value": ["2"], "type": "list"}})

    cleanup = next(v for k, v in world.store.sync_points.items() if k.endswith("filter_cleanup:excluded_groups"))
    assert cleanup["excluded_group_ids"] == ["2"]
