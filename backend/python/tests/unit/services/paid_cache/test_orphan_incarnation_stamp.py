"""The run-start stamp (``createdAtTimestamp``) is compared only for uploaded KB records (D-186).

A connector that builds ``created_at`` from the clock rewrites it on every re-sync of a live record, so comparing it there
would stop live runs (QA-G32B-1). For an uploaded record the stamp is written once and kept by every update path, so a
different stamp is a delete plus a re-upload. Each case drives the real ``DataSourceEntitiesProcessor`` and the real
handler guard; the graph answers with the document the processor wrote.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors as ConnectorsEnum
from app.core.org_context import OrgContext, bind
from app.models.entities import FileRecord, OriginTypes, Record, RecordType
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.orphan import RECHECK_S, current_guard
from app.services.paid_cache.run import clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler
from tests.unit.connectors.core.test_data_source_entities_processor import (
    _make_ctx,
    _make_processor,
    _make_tx_store,
)

ORG, REC = "org-1", "rec-live"
STORED_CREATED = 1_700_000_000_000


def _record(origin: str, **kw: Any) -> FileRecord:  # noqa: ANN401
    base: dict[str, Any] = {
        "org_id": ORG, "external_record_id": "ext-1", "record_name": "a.txt", "origin": origin,
        "connector_name": ConnectorsEnum.KNOWLEDGE_BASE if origin == OriginTypes.UPLOAD.value else ConnectorsEnum.GOOGLE_MAIL,
        "connector_id": "conn-1", "record_type": RecordType.FILE, "version": 1, "mime_type": "text/plain",
        "source_created_at": 1000, "source_updated_at": 2000, "external_revision_id": "r1",
    }
    base.update(kw)
    return FileRecord(is_file=True, extension="txt", size_in_bytes=100, weburl="https://example.com", **base)


def _stored(origin: str) -> FileRecord:
    rec = _record(origin, created_at=STORED_CREATED)
    rec.id = REC
    rec.indexing_status = "IN_PROGRESS"
    return rec


def _incoming(origin: str, path: str) -> FileRecord:
    """What the writer hands the processor. KB paths load the stored record (``get_file_record_by_id``) and edit it;
    connectors build a fresh one with ``created_at`` left at its default (now)."""
    revision = "r2" if path in {"resync_new_revision", "reupload"} else "r1"
    if origin == OriginTypes.UPLOAD.value:
        stored = _stored(origin)
        assert Record.from_arango_base_record(stored.to_arango_base_record()).created_at == STORED_CREATED
        return stored.model_copy(update={
            "external_revision_id": revision, "record_name": "renamed.txt" if path == "rename" else stored.record_name,
        })
    return _record(origin, external_revision_id=revision)


async def _written_doc(origin: str, path: str) -> dict[str, Any]:
    proc = _make_processor()
    tx = _make_tx_store()
    proc.data_store_provider.transaction.return_value = _make_ctx(tx)
    tx.get_person_by_email = AsyncMock(return_value=None)
    tx.upsert_person_by_email = AsyncMock(return_value="person-1")
    tx.get_record_by_external_id = AsyncMock(return_value=_stored(origin))
    incoming = _incoming(origin, path)
    if path in {"metadata_update", "rename"}:
        await proc.on_record_metadata_update(incoming)
    elif path == "reupload":
        await proc.on_record_content_update(incoming)
    else:
        await proc.on_new_records([(incoming, [])])
    assert tx.batch_upsert_records.await_count >= 1
    return tx.batch_upsert_records.await_args_list[-1].args[0][0].to_arango_base_record()


class _Clock:
    t = 1000.0


async def _guard(monkeypatch: pytest.MonkeyPatch, origin: str, graph_doc: dict[str, Any] | None) -> Any:  # noqa: ANN401
    """The handler's own guard for a run that started with the stored record."""
    cache = PaidResultCache(MemoryResultStore(), InProcessLocks())
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    logger = logging.getLogger("test.orphan.stamp")
    event_processor = MagicMock()
    handler = make_record_handler(logger=logger, event_processor=event_processor)
    handler.event_processor.graph_provider.get_document = AsyncMock(side_effect=lambda *_a, **_k: graph_doc)
    started = {"_key": REC, "origin": origin, "createdAtTimestamp": STORED_CREATED, "recordType": "FILE"}
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        guard = await handler._begin_orphan_guard(REC, started)
    assert guard is not None
    guard.clock = lambda: _Clock.t
    guard.mark_read()
    return guard


@pytest.fixture(autouse=True)
def _clean() -> Any:  # noqa: ANN401
    yield
    clear_run()
    _Clock.t = 1000.0


@pytest.mark.parametrize("path", ["metadata_update", "resync_unchanged", "resync_new_revision"])
async def test_a_connector_resync_of_a_live_record_does_not_stop_its_run(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    doc = await _written_doc(OriginTypes.CONNECTOR.value, path)
    # The store and the processor keep the stored stamp on a re-sync.
    assert doc["createdAtTimestamp"] == STORED_CREATED, "a re-sync keeps the stored stamp"
    guard = await _guard(monkeypatch, OriginTypes.CONNECTOR.value, {**doc, "_key": REC})
    _Clock.t += RECHECK_S + 1                                  # the run's next check, 30 s later, no marker at all
    assert await guard.check(None) is False
    assert not guard.gone


@pytest.mark.parametrize("path", ["rename", "reupload"])
async def test_an_uploaded_record_keeps_its_stamp_through_a_rename_and_a_reupload(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    doc = await _written_doc(OriginTypes.UPLOAD.value, path)
    assert doc["createdAtTimestamp"] == STORED_CREATED
    guard = await _guard(monkeypatch, OriginTypes.UPLOAD.value, {**doc, "_key": REC})
    _Clock.t += RECHECK_S + 1
    assert await guard.check(None) is False


async def test_an_uploaded_record_deleted_and_pushed_again_is_a_newer_incarnation(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = await _guard(monkeypatch, OriginTypes.UPLOAD.value, {"_key": REC, "createdAtTimestamp": STORED_CREATED + 5000, "isDeleted": False})
    _Clock.t += RECHECK_S + 1
    assert await guard.check(None) is True


async def test_a_connector_record_deleted_and_created_again_is_not_told_apart_by_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-186, the accepted residual: for a connector record the stamp is not compared, so an old run survives a delete plus
    a quick re-create and pays up to its existing budget (today's cost). The graph read and the newer-delete marker decide."""
    guard = await _guard(monkeypatch, OriginTypes.CONNECTOR.value, {"_key": REC, "createdAtTimestamp": STORED_CREATED + 5000, "isDeleted": False})
    assert guard.created is None
    _Clock.t += RECHECK_S + 1
    assert await guard.check(None) is False


async def test_a_connector_record_really_deleted_is_still_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = await _guard(monkeypatch, OriginTypes.CONNECTOR.value, None)
    _Clock.t += RECHECK_S + 1
    assert await guard.check(None) is True
    assert current_guard(ORG, REC) is guard


# ---- the incarnation id decides for every origin ------------------------------------------------------------------

async def _guard_with_id(monkeypatch: pytest.MonkeyPatch, started_id: str | None, live: dict[str, Any] | None, *, flag: bool = True,
                         origin: str = OriginTypes.CONNECTOR.value) -> Any:  # noqa: ANN401
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true" if flag else "false")
    cache = PaidResultCache(MemoryResultStore(), InProcessLocks())
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    handler = make_record_handler(logger=logging.getLogger("test.orphan.incarnation"), event_processor=MagicMock())
    handler.event_processor.graph_provider.get_document = AsyncMock(side_effect=lambda *_a, **_k: live)
    started = {"_key": REC, "origin": origin, "createdAtTimestamp": STORED_CREATED, "recordType": "FILE"}
    if started_id is not None:
        started["incarnationId"] = started_id
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        guard = await handler._begin_orphan_guard(REC, started)
    guard.clock = lambda: _Clock.t
    guard.mark_read()
    _Clock.t += RECHECK_S + 1
    return guard


def _live(**kw: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"_key": REC, "createdAtTimestamp": STORED_CREATED, "isDeleted": False, **kw}


@pytest.mark.parametrize("origin", [OriginTypes.CONNECTOR.value, OriginTypes.UPLOAD.value])
async def test_a_different_incarnation_stops_the_old_run_even_with_the_same_stamp(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    """S16: a source that reuses its ids (Jira) re-creates a record with the same ``createdAtTimestamp``; the id tells them apart."""
    guard = await _guard_with_id(monkeypatch, "inc-A", _live(incarnationId="inc-B"), origin=origin)
    assert await guard.check(None) is True


async def test_the_same_incarnation_goes_on_whatever_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = await _guard_with_id(monkeypatch, "inc-A", _live(incarnationId="inc-A", createdAtTimestamp=STORED_CREATED + 9))
    assert await guard.check(None) is False


async def test_a_run_that_started_without_an_id_goes_on_when_the_record_gets_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """S17: the backfill gives a legacy record an id mid-run: live."""
    guard = await _guard_with_id(monkeypatch, None, _live(incarnationId="inc-new"))
    assert await guard.check(None) is False


async def test_a_run_that_started_with_an_id_and_finds_none_compares_the_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-create by a writer that leaves the key out is a new incarnation: a different stamp stops the run, the same does not."""
    assert await (await _guard_with_id(monkeypatch, "inc-A", _live(createdAtTimestamp=STORED_CREATED + 5000))).check(None) is True
    assert await (await _guard_with_id(monkeypatch, "inc-A", _live())).check(None) is False


async def test_with_the_flag_off_the_id_is_not_read_and_the_older_rule_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = await _guard_with_id(monkeypatch, "inc-A", _live(incarnationId="inc-B"), flag=False)
    assert guard.incarnation is None
    assert await guard.check(None) is False


async def test_the_start_id_comes_from_the_graph_read_not_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard takes its start values from the record dict it is given (the graph read); nothing else carries an id."""
    guard = await _guard_with_id(monkeypatch, "inc-A", _live(incarnationId="inc-A"))
    assert guard.incarnation == "inc-A" and guard.created_at_start == STORED_CREATED
