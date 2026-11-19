"""Where the installed lifecycle is told about connector purges, stage settling, completed re-extractions and recovery, and the
default does nothing at each of them."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.modules.indexing import extension
from app.modules.indexing import named_entity_completion as completion
from app.modules.indexing.extension import DefaultIndexingLifecycle, IndexingExtension
from app.modules.named_entities.domain.models import NamedEntityExtraction
from tests.support.record_handler import make_record_handler


class Recording(DefaultIndexingLifecycle):
    def __init__(self, *, takes_over: bool = False, recovered: int = 0) -> None:
        self.calls: list[tuple] = []
        self.takes_over, self.recovered = takes_over, recovered

    async def on_connector_purged(self, org_id: str, connector_id: str) -> None:
        self.calls.append(("purged", org_id, connector_id))

    async def on_stage_settled(self, job: object) -> None:
        self.calls.append(("settled", job))

    async def on_reextract_complete(self, record: object, extraction: object) -> bool:
        self.calls.append(("reextract", record["_key"], extraction.status))  # type: ignore[index, attr-defined]
        return self.takes_over

    async def recovery_pass(self) -> int:
        self.calls.append(("recovery",))
        return self.recovered

    async def skip_first_use(self, record: object) -> bool:
        self.calls.append(("first_use", record["_key"]))  # type: ignore[index]
        return True


def _install(life: Recording) -> Recording:
    extension.install(IndexingExtension(lifecycle=life))
    return life


async def _purge(handler: object) -> list:
    return [
        e async for e in handler.process_event(  # type: ignore[attr-defined]
            EventTypes.DELETE_CONNECTOR_EMBEDDINGS.value,
            {"orgId": "org-1", "connectorId": "conn-1", "connectorName": "X", "recordGroupIds": []},
        )
    ]


async def test_a_purged_connector_is_reported_to_the_lifecycle_after_its_points_are_gone() -> None:
    life = _install(Recording())
    handler = make_record_handler()
    pipeline = handler.event_processor.processor.indexing_pipeline
    pipeline.purge_connector = AsyncMock(return_value={"success": True})

    await _purge(handler)

    pipeline.purge_connector.assert_awaited_once()
    assert life.calls == [("purged", "org-1", "conn-1")]


async def test_a_purge_that_did_not_complete_is_not_reported() -> None:
    from app.exceptions.indexing_exceptions import IndexingError

    life = _install(Recording())
    handler = make_record_handler()
    handler.event_processor.processor.indexing_pipeline.purge_connector = AsyncMock(return_value={"success": False})

    with pytest.raises(IndexingError):
        await _purge(handler)

    assert life.calls == []


async def test_with_the_default_lifecycle_a_purge_completes_as_before() -> None:
    handler = make_record_handler()
    handler.event_processor.processor.indexing_pipeline.purge_connector = AsyncMock(return_value={"success": True})

    events = await _purge(handler)

    assert len(events) == 2


_RECORD = {"_key": "r1", "id": "r1", "orgId": "o1", "virtualRecordId": "v1", "contentRev": "rev-a"}
_BLOB = {"block_containers": {"blocks": [{"index": 0, "type": "text", "format": "txt", "data": "Acme hired Bob."}]}}


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=_RECORD)
    graph.stage_state_compare_and_set = AsyncMock(return_value=True)
    return graph


def _sink(extraction: NamedEntityExtraction) -> MagicMock:
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value=_BLOB)
    sink.extract_named_entities = AsyncMock(return_value=extraction)
    sink.persist_named_entities = AsyncMock(return_value="written")
    return sink


async def _complete(sink: MagicMock) -> str:
    context = lambda record, settings: SimpleNamespace(record=record, settings=settings)  # noqa: E731
    with patch("app.events.processor.convert_record_dict_to_record", return_value=SimpleNamespace(id="r1")), \
            patch("app.modules.transformers.transformer.TransformContext", context):
        return await completion._complete_one(_graph(), sink, None, "r1", "o1", 100, 0)


_DONE = NamedEntityExtraction(status="COMPLETED", strategy="single_call", termination_reason="single_call")


async def test_a_completed_re_extraction_the_lifecycle_takes_over_is_not_persisted_here_and_ends_the_job() -> None:
    life = _install(Recording(takes_over=True))
    sink = _sink(_DONE)

    assert await _complete(sink) == "gone"

    assert life.calls == [("reextract", "r1", "COMPLETED")]
    sink.persist_named_entities.assert_not_awaited()


async def test_a_completed_re_extraction_the_lifecycle_declines_is_persisted_as_always() -> None:
    life = _install(Recording(takes_over=False))
    sink = _sink(_DONE)

    assert await _complete(sink) == "written"

    assert life.calls == [("reextract", "r1", "COMPLETED")]
    sink.persist_named_entities.assert_awaited_once()


async def test_a_failed_re_extraction_never_reaches_the_lifecycle() -> None:
    life = _install(Recording(takes_over=True))
    sink = _sink(NamedEntityExtraction(status="FAILED", strategy="single_call"))

    assert await _complete(sink) == "extract_failed"

    assert life.calls == []


async def test_the_stage_runtime_reports_a_settled_stage_to_whatever_lifecycle_is_installed_at_that_moment() -> None:
    """The runtime is built before the extension is installed at start-up, so the hook must be looked up when it fires."""
    from tests.unit.modules.pipeline.test_runtime_adapters import _runtime

    runtime = _runtime()
    life = _install(Recording())
    job = object()

    await runtime.handler._on_stage_settled(job)  # type: ignore[misc]

    assert life.calls == [("settled", job)]


async def test_the_default_lifecycle_swallows_a_settled_stage() -> None:
    from tests.unit.modules.pipeline.test_runtime_adapters import _runtime

    await _runtime().handler._on_stage_settled(object())  # type: ignore[misc]
