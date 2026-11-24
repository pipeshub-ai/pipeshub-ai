"""A mock indexing container and an in-memory graph for tests that run the stale-record scan for real."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames, ProgressStatus


def make_container() -> MagicMock:
    """Build a mock IndexingAppContainer with common providers.

    Includes a default kafka_consumers entry with a mock retry producer so
    recover_in_progress_records() tests have something to publish to
    without each test having to wire it up individually. Tests that need to
    assert against send_event should read it back via
    ``mock_container.kafka_consumers[0][2]``.
    """
    container = MagicMock()
    container.logger.return_value = MagicMock()
    mock_config_service = MagicMock()
    mock_config_service.get_config = AsyncMock(return_value={})
    mock_config_service.close = AsyncMock()
    container.config_service.return_value = mock_config_service
    container.graph_provider = AsyncMock()
    container.connector_off_filter = AsyncMock(return_value=None)
    mock_producer = AsyncMock()
    mock_producer.send_event = AsyncMock(return_value=True)
    mock_consumer = MagicMock()
    mock_consumer.concurrency_manager = None
    mock_consumer.retry_manager = None
    mock_consumer.disposition_sink = None
    mock_consumer._run_on_main_loop = None
    container.kafka_consumers = [("record", mock_consumer, mock_producer)]
    container.pipeline_runtime.return_value = stage_runtime()
    return container


def stage_runtime() -> MagicMock:
    """A pipeline runtime with one stage, as the container provides it."""
    runtime = MagicMock()
    runtime.registry.names.return_value = ("classify",)
    runtime.registry.topic_for.side_effect = lambda name: f"pipeline.{name}"
    runtime.stage_limits = {"classify": 2}
    return runtime


@staticmethod
def stale_scan_graph(records, connector=None, connector_read_fails=False) -> tuple[MagicMock, dict]:
    gp = MagicMock()
    by_key = {r["_key"]: r for r in records}

    async def paginated(*_a, filters=None, **_k) -> list[dict]:
        (field, value), = ((k, v) for k, v in (filters or {}).items() if k != "orgId")
        return [dict(r) for r in by_key.values() if r.get(field) == value]

    async def get_document(doc_id, collection, *, raise_on_error=False, **_k) -> dict | None:
        if collection == CollectionNames.APPS.value:
            if connector_read_fails:
                # Both providers log a failed read and answer None unless asked to raise.
                if raise_on_error:
                    raise ConnectionError("graph unavailable")
                return None
            return connector
        row = by_key.get(doc_id)
        return dict(row) if row else None

    async def update_node(doc_id, _collection, fields) -> bool:
        by_key[doc_id].update(fields)
        return True

    gp.get_documents_paginated = AsyncMock(side_effect=paginated)
    gp.get_document = AsyncMock(side_effect=get_document)
    gp.update_node = AsyncMock(side_effect=update_node)
    gp.get_nodes_by_filters = AsyncMock(return_value=[])
    gp.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    return gp, by_key


@staticmethod
def enrichment_twin(started_at) -> dict:
    return {
        "_key": "twin", "recordName": "q3.pdf", "orgId": "org-1", "origin": "UPLOAD",
        "virtualRecordId": "vr-1", "version": 0,
        "indexingStatus": ProgressStatus.COMPLETED.value,
        "parsingStatus": ProgressStatus.COMPLETED.value,
        "extractionStatus": ProgressStatus.IN_PROGRESS.value,
        "processingStartedAt": started_at,
    }


class FakeConcurrencyManager:
    """The per-record lease of the distributed indexing mode, in memory."""

    def __init__(self, owners: dict | None = None) -> None:
        self.owners = dict(owners or {})

    async def try_acquire(self, pool: str, owner: str, _limit: int, _lease_seconds: float) -> bool:
        if pool in self.owners and self.owners[pool] != owner:
            return False
        self.owners[pool] = owner
        return True

    async def renew(self, pool: str, owner: str, _lease_seconds: float) -> bool:
        return self.owners.get(pool) == owner

    async def release(self, pool: str, owner: str) -> None:
        if self.owners.get(pool) == owner:
            self.owners.pop(pool)
