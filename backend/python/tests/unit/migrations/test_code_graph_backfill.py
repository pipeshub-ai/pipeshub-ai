"""Repos indexed before the code graph, or by an older version of it, are re-indexed once (O-10)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations import code_graph_backfill
from app.migrations.code_graph_backfill import (
    MIGRATION_FLAG_PREFIX,
    find_stale_repos,
    run_code_graph_backfill,
)
from app.modules.code_graph import version
from app.modules.code_graph.version import PARSER_VERSION

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _backfill_on(monkeypatch) -> None:
    monkeypatch.setenv("CODE_GRAPH_BACKFILL", "true")


def _graph(records: list[dict], blocks: dict[str, tuple[int, int]]) -> MagicMock:
    """``blocks``: group -> (all blocks, blocks at the current version)."""
    graph = MagicMock()
    graph.get_documents_paginated = AsyncMock(return_value=records)

    async def count(collection, filters) -> int:
        total, current = blocks.get(filters["recordGroupId"], (0, 0))
        return current if "parserVersion" in filters else total

    graph.count_nodes_by_filters = AsyncMock(side_effect=count)
    return graph


def _record(key: str, group: str, connector: str = "GitHub") -> dict:
    return {"_key": key, "orgId": "org", "connectorId": f"c-{group}",
            "connectorName": connector, "recordGroupId": group}


async def test_a_repo_without_blocks_or_with_old_blocks_is_stale() -> None:
    graph = _graph(
        [_record("r1", "never"), _record("r2", "old"), _record("r3", "mixed"), _record("r4", "ok")],
        {"old": (10, 0), "mixed": (10, 4), "ok": (10, 10)},
    )
    stale = await find_stale_repos(graph)
    assert [s.record_group_id for s in stale] == ["mixed", "never", "old"]
    query = graph.get_documents_paginated.await_args.kwargs
    assert query["filters"] == {"recordType": "CODE_FILE", "indexingStatus": "COMPLETED"}


async def test_each_stale_repo_is_asked_to_reindex_once_per_version() -> None:
    graph = _graph([_record("r1", "never", "GitHub Teams"), _record("r2", "ok")], {"ok": (5, 5)})
    kafka = MagicMock()
    kafka.publish_event = AsyncMock()
    config = MagicMock()
    config.get_config = AsyncMock(return_value=None)
    config.set_config = AsyncMock(return_value=True)

    assert await run_code_graph_backfill(config, graph, kafka, MagicMock(), pause_seconds=0) == 1
    topic, event = kafka.publish_event.await_args.args
    assert topic == "sync-events"
    assert event["eventType"] == "githubteams.reindex"
    assert event["payload"] == {"orgId": "org", "connectorId": "c-never", "connector": "githubteams",
                                "recordGroupId": "never", "depth": -1}
    config.set_config.assert_awaited_once_with(f"{MIGRATION_FLAG_PREFIX}{PARSER_VERSION}",
                                               {"done": True, "repos": 1})

    config.get_config = AsyncMock(return_value={"done": True})
    kafka.publish_event.reset_mock()
    assert await run_code_graph_backfill(config, graph, kafka, MagicMock(), pause_seconds=0) == 0
    kafka.publish_event.assert_not_awaited()


def _blocks_at(parser_version: str) -> MagicMock:
    """One repo whose blocks all carry ``parser_version``; counts honour the filter."""
    graph = MagicMock()
    graph.get_documents_paginated = AsyncMock(return_value=[_record("r1", "repo")])

    async def count(collection, filters) -> int:
        wanted = filters.get("parserVersion")
        return 0 if wanted is not None and wanted != parser_version else 10

    graph.count_nodes_by_filters = AsyncMock(side_effect=count)
    return graph


async def test_an_edge_rules_bump_reindexes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(version, "EDGE_RULES_VERSION", "2099.01.01")
    assert await find_stale_repos(_blocks_at(PARSER_VERSION)) == []


async def test_a_parser_bump_marks_the_repo_stale(monkeypatch) -> None:
    graph = _blocks_at(PARSER_VERSION)
    monkeypatch.setattr(code_graph_backfill, "PARSER_VERSION", "2099.01.01")
    assert [s.record_group_id for s in await find_stale_repos(graph)] == ["repo"]


async def test_without_the_flag_nothing_is_reindexed(monkeypatch) -> None:
    """Off by default: re-indexing every repo on deploy is an admin's call."""
    monkeypatch.delenv("CODE_GRAPH_BACKFILL")
    graph = _graph([_record("r1", "never")], {})
    kafka = MagicMock()
    kafka.publish_event = AsyncMock()
    log = MagicMock()
    assert await run_code_graph_backfill(MagicMock(), graph, kafka, log, pause_seconds=0) == 0
    kafka.publish_event.assert_not_awaited()
    graph.get_documents_paginated.assert_not_awaited()
    assert "CODE_GRAPH_BACKFILL=true" in log.info.call_args.args[0]


async def test_the_kill_switch_skips_it_even_with_the_flag(monkeypatch) -> None:
    from app.modules.code_graph import enabled
    monkeypatch.setattr(enabled, "CODE_GRAPH_ENABLED", False)
    graph = _graph([_record("r1", "never")], {})
    kafka = MagicMock()
    kafka.publish_event = AsyncMock()
    assert await run_code_graph_backfill(MagicMock(), graph, kafka, MagicMock(), pause_seconds=0) == 0
    kafka.publish_event.assert_not_awaited()
