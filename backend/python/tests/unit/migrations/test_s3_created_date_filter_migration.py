"""PP-101: the S3 and MinIO Created Date filter judged LastModified; the startup
migration moves its bounds into Modified Date, so each connector keeps its scope."""

import copy
import logging
from unittest.mock import AsyncMock

import pytest

from app.migrations.s3_created_date_filter_migration import (
    MIGRATION_FLAG_KEY,
    run_s3_created_date_filter_migration,
)

LOGGER = logging.getLogger("test-s3-created-filter-migration")
JAN, FEB, MAR, APR = 1767225600000, 1769904000000, 1772323200000, 1775001600000


def _date(operator: str, start: int | None = None, end: int | None = None) -> dict:
    return {"operator": operator, "type": "datetime", "value": {"start": start, "end": end}}


def _config(values: dict) -> dict:
    return {"auth": {"bucket": "b1"}, "filters": {"sync": {"values": values}, "indexing": {"values": {}}}}


class _MemoryConfigService:
    def __init__(self, store: dict, failing_reads: frozenset[str] = frozenset()) -> None:
        self.store = store
        self.failing_reads = failing_reads
        self.writes: list[str] = []

    async def get_config(self, path, default=None, use_cache=True, *, raise_on_error=False) -> object:
        if path in self.failing_reads:
            if raise_on_error:
                raise ConnectionError(f"store did not answer for {path}")
            return default
        return copy.deepcopy(self.store.get(path, default))

    async def set_config(self, path, value) -> bool:
        self.writes.append(path)
        self.store[path] = copy.deepcopy(value)
        return True


def _graph(**connectors_by_type: list[str]) -> AsyncMock:
    graph = AsyncMock()
    graph.get_nodes_by_filters = AsyncMock(
        side_effect=lambda collection, filters, **_: [{"_key": c} for c in connectors_by_type.get(filters["type"], [])]
    )
    return graph


def _path(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/config"


def _values(store: dict, connector_id: str) -> dict:
    return store[_path(connector_id)]["filters"]["sync"]["values"]


@pytest.mark.asyncio
async def test_a_created_filter_alone_becomes_the_modified_filter() -> None:
    store = {_path("s3-1"): _config({"created": _date("is_after", FEB)})}

    result = await run_s3_created_date_filter_migration(_MemoryConfigService(store), _graph(S3=["s3-1"]), LOGGER)

    assert result == {"success": True, "skipped": False, "migrated": 1, "failures": 0}
    assert _values(store, "s3-1") == {"modified": _date("is_after", FEB)}
    assert store[MIGRATION_FLAG_KEY] == {"done": True, "migrated": 1}


@pytest.mark.asyncio
async def test_two_absolute_filters_become_their_intersection() -> None:
    store = {
        _path("minio-1"): _config({
            "created": _date("is_between", JAN, MAR), "modified": _date("is_after", FEB), "file_extensions": {"x": 1},
        }),
    }

    await run_s3_created_date_filter_migration(_MemoryConfigService(store), _graph(MinIO=["minio-1"]), LOGGER)

    assert _values(store, "minio-1") == {"modified": _date("is_between", FEB, MAR), "file_extensions": {"x": 1}}


@pytest.mark.asyncio
async def test_a_relative_created_filter_next_to_a_modified_one_is_dropped() -> None:
    store = {_path("s3-1"): _config({"created": _date("last_7_days"), "modified": _date("is_before", APR)})}

    await run_s3_created_date_filter_migration(_MemoryConfigService(store), _graph(S3=["s3-1"]), LOGGER)

    assert _values(store, "s3-1") == {"modified": _date("is_before", APR)}


@pytest.mark.asyncio
async def test_a_connector_without_a_created_filter_is_not_rewritten_and_the_flag_skips_the_next_run() -> None:
    store = {_path("s3-1"): _config({"modified": _date("is_after", FEB)})}
    config = _MemoryConfigService(store)

    await run_s3_created_date_filter_migration(config, _graph(S3=["s3-1"]), LOGGER)
    again = await run_s3_created_date_filter_migration(config, _graph(S3=["s3-1"]), LOGGER)

    assert config.writes == [MIGRATION_FLAG_KEY]
    assert again["skipped"] is True


@pytest.mark.asyncio
async def test_an_unreadable_config_leaves_the_flag_unset_so_the_next_start_retries() -> None:
    store = {_path("s3-1"): _config({"created": _date("is_after", FEB)})}

    result = await run_s3_created_date_filter_migration(
        _MemoryConfigService(store, failing_reads=frozenset({_path("s3-1")})), _graph(S3=["s3-1"]), LOGGER,
    )

    assert result["failures"] == 1
    assert MIGRATION_FLAG_KEY not in store
    assert "created" in _values(store, "s3-1")
