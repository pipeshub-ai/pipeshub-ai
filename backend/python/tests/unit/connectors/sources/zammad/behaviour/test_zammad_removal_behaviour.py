"""Zammad tickets leave the index when they leave Zammad or the sync filters.

The connector's own sync code runs for real (group listing, filters, ticket
search, attachments, sync points, removal); Zammad and our stores are the
in-memory fakes in ``zammad_behaviour_fakes``.
"""

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeRecordsDb,
    FakeStore,
    FakeZammad,
    epoch_ms,
)

from app.connectors.sources.zammad import connector as zammad_connector
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
async def world() -> AsyncIterator[World]:
    w = World()
    w.zammad.add_ticket(10, 1, day=1)
    w.zammad.add_ticket(11, 1, day=2, attachments=1)
    w.zammad.add_ticket(20, 2, day=3)
    await w.sync()
    assert w.db.external_ids() == {"10", "11", "11_1_1", "20"}
    yield w
    assert w.zammad.refused_searches == []


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


def _checkpoint(world: World, group_name: str) -> object:
    return next((v.get("last_sync_time") for k, v in world.store.sync_points.items() if k.endswith(group_name)), None)


async def test_a_read_back_that_fails_once_is_repeated_and_the_ticket_goes_on_the_next_sync(world: World) -> None:
    world.zammad.delete_ticket(11)
    world.zammad.ticket_read_status[11] = 503
    await world.sync()
    assert "11" in world.db.external_ids()

    del world.zammad.ticket_read_status[11]
    await world.sync()
    assert world.db.external_ids() == {"10", "20"}


async def test_a_failed_search_page_keeps_the_group_checkpoint_until_a_sync_reads_it_all(world: World) -> None:
    before = _checkpoint(world, "Support")
    # Two pages of changes; search pages come newest first, so the newest lands on page one.
    world.zammad.add_ticket(599, 1, day=20)
    for ticket_id in range(500, 560):
        world.zammad.add_ticket(ticket_id, 1, day=9, minute=ticket_id)
    world.zammad.fail_search_at_offset = 50

    await world.sync()
    assert "599" in world.db.external_ids()
    assert _checkpoint(world, "Support") == before

    world.zammad.fail_search_at_offset = None
    await world.sync()
    assert all(str(t) in world.db.external_ids() for t in range(500, 560))
    assert _checkpoint(world, "Support") > before


async def test_a_ticket_whose_articles_fail_keeps_the_checkpoint_and_is_read_again(world: World) -> None:
    before = _checkpoint(world, "Support")
    world.zammad.add_ticket(12, 1, day=9, attachments=1)
    world.zammad.add_ticket(13, 1, day=10)
    world.zammad.fail_articles_for.add(12)

    await world.sync()
    # The other ticket lands, but the group's checkpoint stays behind the one that failed.
    assert "13" in world.db.external_ids()
    assert "12" not in world.db.external_ids()
    assert _checkpoint(world, "Support") == before

    world.zammad.fail_articles_for.clear()
    await world.sync()
    assert {"12", "12_1_1"} <= world.db.external_ids()


def _search_window(world: World, monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    world.zammad.result_window = size
    monkeypatch.setattr(zammad_connector, "SEARCH_RESULT_WINDOW", size)


async def test_a_group_past_the_search_window_syncs_every_ticket_and_moves_its_checkpoint(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    for ticket_id in range(100, 400):
        world.zammad.add_ticket(ticket_id, 1, day=4, minute=2 * ticket_id)

    await world.save_filters({})

    assert all(str(t) in world.db.external_ids() for t in range(100, 400))
    assert _checkpoint(world, "Support") is not None


async def test_a_deleted_ticket_in_a_group_past_the_search_window_is_removed(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    for ticket_id in range(100, 400):
        world.zammad.add_ticket(ticket_id, 1, day=4, minute=2 * ticket_id)
    await world.save_filters({})
    assert "399" in world.db.external_ids()

    world.zammad.delete_ticket(399)
    await world.sync()

    assert "399" not in world.db.external_ids()
    assert all(str(t) in world.db.external_ids() for t in range(100, 399))


async def test_a_failed_id_range_keeps_its_tickets_and_still_removes_elsewhere(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    for ticket_id in range(100, 300):
        world.zammad.add_ticket(ticket_id, 1, day=4, minute=2 * ticket_id)
    await world.save_filters({})
    world.zammad.delete_ticket(11)
    world.zammad.delete_ticket(299)
    world.zammad.fail_search = lambda query: "TO 299]" in query

    await world.sync()

    assert "11" not in world.db.external_ids()
    assert "299" in world.db.external_ids()


async def test_a_ticket_moved_out_of_an_excluded_group_is_moved_not_removed(world: World) -> None:
    world.zammad.move_ticket(20, 1, day=6)
    world.zammad.unindexed.add(20)  # the destination group's search has not caught up

    await world.save_filters({"group_ids": {"operator": "not_in", "value": ["2"], "type": "list"}})

    assert "20" in world.db.external_ids()
    assert world.db.records["20"].external_record_group_id == "group_1"
    assert "20" not in world.db.deleted


async def test_an_excluded_group_ticket_that_cannot_be_read_back_is_retried(world: World) -> None:
    world.zammad.ticket_read_status[20] = 503
    await world.save_filters({"group_ids": {"operator": "not_in", "value": ["2"], "type": "list"}})
    assert "20" in world.db.external_ids()

    del world.zammad.ticket_read_status[20]
    await world.sync()
    assert "20" not in world.db.external_ids()


def _group_point(world: World, group_name: str) -> dict:
    return next((v for k, v in world.store.sync_points.items() if k.endswith(group_name)), {})


async def test_a_burst_past_the_search_window_at_one_timestamp_is_read_by_ticket_id(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    for ticket_id in range(1000, 1150):
        world.zammad.add_ticket(ticket_id, 1, day=5)
    world.zammad.add_ticket(1200, 1, day=6)

    await world.save_filters({})

    assert all(str(t) in world.db.external_ids() for t in [*range(1000, 1150), 1200])
    assert _checkpoint(world, "Support") > epoch_ms(6)
    assert not _group_point(world, "Support").get("burst_next_id")


async def test_a_burst_read_that_fails_part_way_carries_on_from_the_id_it_reached(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    for ticket_id in range(1000, 1150):
        world.zammad.add_ticket(ticket_id, 1, day=5)
    world.zammad.add_ticket(1200, 1, day=6)
    world.zammad.fail_search = lambda query: "id:[1100 TO 1109]" in query

    await world.save_filters({})
    assert all(str(t) in world.db.external_ids() for t in range(1000, 1100))
    assert "1120" not in world.db.external_ids() and "1200" not in world.db.external_ids()
    assert _group_point(world, "Support").get("burst_next_id") == 1100
    assert (_checkpoint(world, "Support") or 0) < epoch_ms(5)

    world.zammad.fail_search = lambda _query: False
    world.zammad.search_queries.clear()
    await world.sync()

    assert all(str(t) in world.db.external_ids() for t in [*range(1100, 1150), 1200])
    burst_reads = [q for q in world.zammad.search_queries if "updated_at" in q and " AND id:[" in q]
    assert burst_reads and not any("id:[1000 TO" in q for q in burst_reads), "ranges already read are not read again"
    assert _group_point(world, "Support").get("burst_next_id") == 0
    assert _checkpoint(world, "Support") > epoch_ms(6)


async def test_a_window_past_the_search_window_is_split_after_one_probe_not_after_paging_to_its_end(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 300)
    for ticket_id in range(100, 500):
        world.zammad.add_ticket(ticket_id, 1, day=4, minute=2 * ticket_id)

    world.zammad.search_queries.clear()
    await world.save_filters({})

    assert all(str(t) in world.db.external_ids() for t in range(100, 500))
    # The first page, then one read at the window's last slot; not six pages up to it.
    assert world.zammad.search_queries.count("group_id:1") == 2


async def test_a_failed_window_probe_keeps_the_group_checkpoint(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 300)
    before = _checkpoint(world, "Support")
    for ticket_id in range(100, 500):
        world.zammad.add_ticket(ticket_id, 1, day=4, minute=2 * ticket_id)
    world.zammad.fail_search_at_offset = 299

    await world.sync()
    assert _checkpoint(world, "Support") == before

    world.zammad.fail_search_at_offset = None
    await world.sync()
    assert all(str(t) in world.db.external_ids() for t in range(100, 500))
    assert _checkpoint(world, "Support") > before


async def test_a_ticket_that_never_reads_holds_the_checkpoint_for_three_syncs_then_lets_it_move(world: World) -> None:
    before = _checkpoint(world, "Support")
    world.zammad.add_ticket(12, 1, day=9, attachments=1)
    world.zammad.add_ticket(13, 1, day=10)
    world.zammad.fail_articles_for.add(12)

    await world.sync()
    await world.sync()
    assert _checkpoint(world, "Support") == before
    assert _group_point(world, "Support")["read_failure_counts"] == [2]

    await world.sync()
    assert "13" in world.db.external_ids() and "12" not in world.db.external_ids()
    assert _checkpoint(world, "Support") > epoch_ms(10)
    assert _group_point(world, "Support")["read_failure_versions"] == []

    # An edit in Zammad brings it back into the search, with a fresh count.
    world.zammad.fail_articles_for.clear()
    world.zammad.move_ticket(12, 1, day=20)
    await world.sync()
    assert {"12", "12_1_1"} <= world.db.external_ids()


async def test_a_failed_ticket_in_another_window_does_not_stop_a_burst_read_by_id(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _search_window(world, monkeypatch, 60)
    world.zammad.add_ticket(12, 1, day=3)
    world.zammad.fail_articles_for.add(12)
    for ticket_id in range(1000, 1150):
        world.zammad.add_ticket(ticket_id, 1, day=5)

    await world.save_filters({})

    assert all(str(t) in world.db.external_ids() for t in range(1000, 1150))
    assert "12" not in world.db.external_ids()


async def test_a_ticket_moved_into_a_group_that_is_not_synced_is_removed(world: World) -> None:
    world.zammad.groups[3] = "Archive"
    world.zammad.inactive.add(3)
    world.zammad.move_ticket(10, 3, day=5)

    await world.sync()

    assert "10" not in world.db.external_ids()
    assert "10" in world.db.deleted
    assert not any(r.external_record_group_id == "group_3" for r in world.db.records.values())


async def test_an_unchanged_group_is_checked_with_one_count_and_no_listing(world: World) -> None:
    world.zammad.search_queries.clear()
    world.zammad.count_calls.clear()

    await world.sync()

    assert len(world.zammad.count_calls) == 2  # one per group holding tickets
    assert not any(" AND id:[" in q for q in world.zammad.search_queries)


async def test_a_deleted_ticket_is_found_by_its_short_count_and_only_its_chunk_is_listed(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zammad_connector, "TICKET_ID_COUNT_CHUNK", 2)
    world.zammad.add_ticket(12, 1, day=4)
    world.zammad.add_ticket(13, 1, day=4)
    await world.sync()
    world.zammad.delete_ticket(13)
    world.zammad.search_queries.clear()

    await world.sync()

    assert "13" not in world.db.external_ids() and {"10", "11", "12"} <= world.db.external_ids()
    listings = [q for q in world.zammad.search_queries if " AND id:[" in q]
    assert listings and all("id:[12 TO 13]" in q for q in listings)


async def test_a_zammad_that_cannot_count_still_finds_deletions_and_is_asked_once_per_sync(world: World) -> None:
    world.zammad.supports_count = False
    world.zammad.count_calls.clear()
    world.zammad.delete_ticket(11)

    await world.sync()

    assert world.db.external_ids() == {"10", "20"}
    assert len(world.zammad.count_calls) == 1
