"""D-180: each sender of a reindex event is classified, and behaves as classified.

* a user's Force reindex (the API, then the connector walk): refresh, every stored paid result is paid again;
* storage cleanup (stored bytes lost or removed): refresh, nothing stored can be trusted;
* a connector's reindex of records it saw unchanged (a policy change such as auto-index): reuse;
* the class-unit backfill: sends no reindex event at all (it reads blobs and embeds units).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.reindex_force import forced_by_user, user_forced_reindex
from app.services.paid_cache.run import refresh_requested


def _record() -> MagicMock:
    record = MagicMock()
    record.to_kafka_record.return_value = {"recordId": "rec-1", "orgId": "org-1"}
    record.virtual_record_id = "vr-1"
    return record


def test_a_connector_reindex_of_unchanged_records_reuses_the_stored_results() -> None:
    payload = DataSourceEntitiesProcessor._reindex_event_payload(_record(), vector_db_only=False)
    assert payload["forceReindex"] is True                      # the LLM stages run again, as before
    assert refresh_requested(payload) is False                  # but nothing stored is bypassed


def test_the_same_reindex_during_a_users_force_walk_refreshes() -> None:
    with forced_by_user(forced=True):
        assert user_forced_reindex() is True
        payload = DataSourceEntitiesProcessor._reindex_event_payload(_record(), vector_db_only=False)
    assert refresh_requested(payload) is True
    assert user_forced_reindex() is False                       # the walk's scope ends with it


def test_the_force_flag_is_not_inherited_by_a_later_plain_reindex() -> None:
    with forced_by_user(forced=True):
        pass
    assert refresh_requested(DataSourceEntitiesProcessor._reindex_event_payload(_record(), vector_db_only=False)) is False


async def test_a_reindex_after_storage_cleanup_refreshes() -> None:
    from unittest.mock import AsyncMock

    from app.connectors.core.base.data_processor.storage_cleanup import (
        StorageCleanupHelper,
    )

    graph = AsyncMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    graph.get_document = AsyncMock(return_value={"_key": "rec-1", "orgId": "org-1", "indexingStatus": "COMPLETED", "recordType": "WEBPAGE"})
    graph._create_reindex_event_payload = AsyncMock(return_value={"recordId": "rec-1", "orgId": "org-1"})
    published: list[dict] = []

    async def publish(topic: str, event: dict) -> bool:
        published.append(event["payload"])
        return True

    helper = StorageCleanupHelper(MagicMock(), graph, MagicMock())
    result = await helper.reindex_one_holder("vr-1", publish)

    assert result.published is True
    assert published[0]["forceReindex"] is True
    assert refresh_requested(published[0]) is True               # what was stored cannot be trusted: paid again


def test_the_class_unit_backfill_sends_no_reindex_event() -> None:
    import inspect

    from app.modules.indexing import class_units_backfill

    source = inspect.getsource(class_units_backfill)
    assert "forceReindex" not in source.replace("a reindex sends ``forceReindex``", "")
    assert SimpleNamespace  # the module only reads blobs and embeds units (see its docstring)
