"""A reindex leaves out records the connector's current indexing filters exclude.

``AUTO_INDEX_OFF`` marks both "waiting to be indexed by hand" and "excluded by an
indexing filter", so the reindex asks the connector which of the selected records
its filters still keep out, whatever entry point started it.
"""

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.connectors.services.event_service import EventService
from tests.unit.connectors.services.coordinator_stub import installed_stub


@pytest.fixture(autouse=True)
def stub_lease_manager() -> Iterator[object]:
    with installed_stub() as stub:
        yield stub


@pytest.fixture
def service() -> EventService:
    gp = AsyncMock()
    gp.update_indexing_status_for_record_ids = AsyncMock()
    return EventService(MagicMock(spec=logging.Logger), MagicMock(), gp)


def _record(record_id: str) -> MagicMock:
    return MagicMock(id=record_id, is_placeholder=False, mime_type="text/plain")


class _Connector:
    """Reports ``excluded`` as kept out by its filters; records what it is asked to reindex."""

    def __init__(self, excluded: set[str]) -> None:
        self.excluded = excluded
        self.reindex_records = AsyncMock()

    async def records_excluded_by_indexing_filters(self, records) -> set[str]:
        return {r.id for r in records if r.id in self.excluded}


async def _run(service, connector, **mode) -> None:
    kwargs = {
        "connector": connector, "connector_name": "gitlab", "connector_id": "c1", "org_id": "org1",
        "record_id": None, "record_group_id": None, "depth": 0, "user_key": None, "status_filters": None,
    }
    kwargs.update(mode)
    await service._run_reindex(**kwargs)


def _reindexed_ids(connector) -> list[str]:
    return [r.id for call in connector.reindex_records.await_args_list for r in call.args[0]]


def _status_writes(service) -> dict[str, list[str]]:
    writes: dict[str, list[str]] = {}
    for call in service.graph_provider.update_indexing_status_for_record_ids.await_args_list:
        ids, status = call.args
        writes.setdefault(status, []).extend(ids)
    return writes


@pytest.mark.asyncio
async def test_manual_index_skips_records_excluded_by_filters(service) -> None:
    service.graph_provider.get_records_by_status = AsyncMock(
        return_value=[_record("issue"), _record("merge-request")]
    )
    connector = _Connector(excluded={"merge-request"})

    await _run(service, connector, status_filters=[ProgressStatus.AUTO_INDEX_OFF.value])

    assert _reindexed_ids(connector) == ["issue"]
    # The excluded record keeps its AUTO_INDEX_OFF status: nothing rewrites it.
    assert _status_writes(service) == {ProgressStatus.NOT_STARTED.value: ["issue"]}


@pytest.mark.asyncio
async def test_record_group_reindex_skips_records_excluded_by_filters(service) -> None:
    service.graph_provider.get_records_by_record_group = AsyncMock(
        return_value=[_record("code-file"), _record("test-file")]
    )
    connector = _Connector(excluded={"test-file"})

    await _run(service, connector, record_group_id="project-1", depth=-1)

    assert _reindexed_ids(connector) == ["code-file"]


@pytest.mark.asyncio
async def test_single_record_reindex_skips_a_record_excluded_by_filters(service) -> None:
    service.graph_provider.get_records_by_parent_record = AsyncMock(
        return_value=[_record("merge-request")]
    )
    connector = _Connector(excluded={"merge-request"})

    await _run(service, connector, record_id="merge-request", depth=0)

    connector.reindex_records.assert_not_awaited()
    assert _status_writes(service) == {}


@pytest.mark.asyncio
async def test_records_no_filter_excludes_are_all_reindexed(service) -> None:
    service.graph_provider.get_records_by_status = AsyncMock(
        return_value=[_record("a"), _record("b")]
    )
    connector = _Connector(excluded=set())

    await _run(service, connector, status_filters=[ProgressStatus.FAILED.value])

    assert _reindexed_ids(connector) == ["a", "b"]
