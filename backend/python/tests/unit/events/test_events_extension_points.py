"""``on_event`` consults the installed extension at the places a record's indexing can differ, and does exactly what it did before
when nothing is installed."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import MimeTypes
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.indexing import extension
from app.modules.indexing.extension import (
    Admit,
    DefaultIndexingLifecycle,
    DefaultLongDocumentPolicy,
    DefaultRecordOwnership,
    IndexingExtension,
    RecordOwnerUnknown,
    Stop,
)
from tests.unit.events.test_events import _drain
from tests.unit.events.test_events_resume import _events, _setup

pytestmark = pytest.mark.asyncio


class Spy(DefaultIndexingLifecycle):
    def __init__(self, admission: Admit | Stop | None = None) -> None:
        self.admitted: list[object] = []
        self.parsed: list[object] = []
        self._admission = admission or Admit()

    async def admit(self, record: object, graph: object) -> Admit | Stop:
        self.admitted.append(record)
        return self._admission

    async def after_parse(self, record: object, graph: object, logger: object) -> None:
        self.parsed.append(record)


async def test_the_record_is_primed_and_admitted_before_anything_is_written() -> None:
    primed: list[object] = []

    class Own(DefaultRecordOwnership):
        async def prime(self, records: list, graph: object) -> None:
            primed.extend(r["_key"] for r in records)

    life = Spy()
    extension.install(IndexingExtension(ownership=Own(), lifecycle=life))
    ep, _processor, _gp, event = _setup()

    await _drain(ep.on_event(event))

    assert primed == ["rec-1"]
    assert [r["_key"] for r in life.admitted] == ["rec-1"]  # type: ignore[index]


async def test_an_owner_that_cannot_be_read_fails_the_attempt_so_it_is_retried_and_nothing_is_indexed() -> None:
    class Own(DefaultRecordOwnership):
        async def prime(self, records: list, graph: object) -> None:
            raise RecordOwnerUnknown("not readable now")

    extension.install(IndexingExtension(ownership=Own()))
    ep, processor, gp, event = _setup()

    with pytest.raises(IndexingError) as raised:
        await _drain(ep.on_event(event))

    assert raised.value.details["record_id"] == "rec-1"
    processor.process_delimited_document.assert_not_called()
    processor.resume_from_checkpoint.assert_not_called()
    assert not any("indexingStatus" in call.args[2] for call in gp.update_node.await_args_list)


async def test_a_stop_from_the_lifecycle_writes_its_status_and_reason_and_indexes_nothing() -> None:
    extension.install(IndexingExtension(lifecycle=Spy(Stop("AUTO_INDEX_OFF", "held back"))))
    ep, processor, gp, event = _setup()

    items = await _drain(ep.on_event(event))

    assert _events(items) == ["PARSING_COMPLETE", "INDEXING_COMPLETE"]
    written = [call.args[2] for call in gp.update_node.await_args_list]
    assert {"indexingStatus": "AUTO_INDEX_OFF", "reason": "held back"}.items() <= written[-1].items()
    processor.process_delimited_document.assert_not_called()
    processor.resume_from_checkpoint.assert_not_called()
    ep._check_duplicate_by_md5.assert_not_awaited()


async def test_the_default_lifecycle_admits_and_the_parse_notifies_once() -> None:
    life = Spy()
    extension.install(IndexingExtension(lifecycle=life))
    ep, processor, _gp, event = _setup(delivery_id=None)

    await _drain(ep.on_event(event))

    processor.process_delimited_document.assert_called_once()
    assert len(life.admitted) == 1


async def test_with_nothing_installed_a_resumable_event_resumes_as_before() -> None:
    ep, processor, _gp, event = _setup()

    await _drain(ep.on_event(event))

    processor.load_checkpoint.assert_awaited_once()
    processor.resume_from_checkpoint.assert_called_once()


class StandIn(DefaultLongDocumentPolicy):
    def __init__(self) -> None:
        self.asked: list[tuple[Any, Any, Any]] = []

    async def holds_stand_in(self, vrid: str | None, extension: str | None, mime_type: str | None) -> bool:
        self.asked.append((vrid, extension, mime_type))
        return True


async def test_a_virtual_record_held_by_a_stand_in_never_resumes_from_stored_blocks() -> None:
    policy = StandIn()
    extension.install(IndexingExtension(policy=policy))
    ep, processor, _gp, event = _setup(mime=MimeTypes.CSV.value)

    await _drain(ep.on_event(event))

    processor.load_checkpoint.assert_not_awaited()
    processor.resume_from_checkpoint.assert_not_called()
    processor.process_delimited_document.assert_called_once()
    assert policy.asked and policy.asked[0][0] == "vr-1"


def _vrid_indexed_under(processor: Any) -> str:  # noqa: ANN401
    call = processor.process_delimited_document.call_args
    return next(a for a in (*call.args, *call.kwargs.values()) if isinstance(a, str) and (a == "vr-1" or len(a) == 36))


@pytest.mark.parametrize(("stand_in", "rebuilt_in_place"), [(False, True), (True, False)])
async def test_a_dedup_that_rebuilds_a_shared_id_in_place_is_overruled_when_a_stand_in_holds_it(
    stand_in: bool, rebuilt_in_place: bool
) -> None:
    from app.events.dedup import DedupDecision

    if stand_in:
        extension.install(IndexingExtension(policy=StandIn()))
    ep, processor, gp, event = _setup(delivery_id=None, event_type="reindexRecord")
    ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision(rebuild_shared_vrid=True))
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "rec-2"])  # shared: not rewritten in place

    await _drain(ep.on_event(event))

    processor.process_delimited_document.assert_called_once()
    assert (_vrid_indexed_under(processor) == "vr-1") is rebuilt_in_place
