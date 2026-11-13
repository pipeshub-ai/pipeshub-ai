"""``EventProcessor.on_event`` and an interrupted embedding run.

A retry of the same event resumes from the stored blocks; nothing else does. The marker is read from the
node as it was before the event wrote anything, and the identity compared is the delivery's, so a
reindex, an update or a user's second click starts from scratch.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.models.blocks import BlocksContainer
from app.modules.indexing.embed_resume import (
    DELIVERY_ID_KEY,
    EMBED_FIELDS,
    chain_end_fields,
    current_attempt,
    end_attempt,
)
from app.modules.pipeline.fingerprint import content_revision
from tests.unit.events.test_events import (
    DedupDecision,
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

CONTENT = b"a,b\n1,2\n"
REV = content_revision(CONTENT)
VRID = "vr-1"


@pytest.fixture(autouse=True)
def _no_attempt() -> Iterator[None]:
    end_attempt()
    yield
    end_attempt()


def _marker(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    marker = {
        "embedVrid": VRID, "embedRev": REV, "embedKey": "k", "embedDigest": "dig", "embedTotal": 9,
        "embedDeliveryId": "d1", "embedStartedMs": 1, "embedSeconds": 0, "embedUncounted": 0, "embedDone": 0,
    }
    marker.update(overrides)
    return marker


def _setup(
    node: dict[str, Any] | None = None, *, mime: str = MimeTypes.CSV.value, event_type: str = "newRecord",
    delivery_id: str | None = "d1", force: bool = False,
) -> tuple[Any, Any, Any, dict[str, Any]]:
    ep, _logger, processor, gp = _make_event_processor()
    base = {
        "_key": "rec-1", "recordType": "FILE", "orgId": "org-1", "indexingStatus": "QUEUED",
        "virtualRecordId": VRID, "extractionStatus": "NOT_STARTED", **_marker(),
    }
    base.update(node or {})
    gp.get_document.return_value = base
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    processor.process_delimited_document = MagicMock(side_effect=_mock_processor_gen)
    processor.load_checkpoint = AsyncMock(return_value=(BlocksContainer(), None))
    processor.resume_from_checkpoint = MagicMock(side_effect=_mock_processor_gen)
    ep._spawn_abandoned_vrid_cleanup = MagicMock()
    ep._rewrite_or_delete_vrid_vectors = AsyncMock()
    ep._check_duplicate_by_md5 = AsyncMock(return_value=DedupDecision())
    ep.stage_ingress = None
    event = _make_event_payload(mime_type=mime, event_type=event_type, virtual_record_id=VRID, buffer=CONTENT)
    if delivery_id:
        event["payload"][DELIVERY_ID_KEY] = delivery_id
    if force:
        event["payload"]["forceReindex"] = True
    return ep, processor, gp, event


def _events(items: list[Any]) -> list[str]:
    return [i.event.name for i in items]


@pytest.mark.asyncio
class TestResume:
    async def test_the_same_event_skips_the_parse_and_loads_the_stored_blocks(self) -> None:
        # Kills: a resume that parses again; one that takes a parse permit.
        ep, processor, gp, event = _setup()

        items = await _drain(ep.on_event(event))

        processor.load_checkpoint.assert_awaited_once_with(VRID, "org-1", REV)
        processor.resume_from_checkpoint.assert_called_once()
        processor.process_delimited_document.assert_not_called()
        assert "START_PARSING" not in _events(items)
        assert current_attempt() is not None and current_attempt().resume

    async def test_the_vrid_of_the_interrupted_run_is_kept_and_not_abandoned(self) -> None:
        # Kills: minting a vrid for a non-reconciled type, which would orphan every point written so far.
        ep, _processor, gp, event = _setup(event_type="updateRecord")

        await _drain(ep.on_event(event))

        ep._spawn_abandoned_vrid_cleanup.assert_not_called()
        ep._rewrite_or_delete_vrid_vectors.assert_not_awaited()
        assert not any("virtualRecordId" in call.args[2] for call in gp.update_node.await_args_list)
        assert current_attempt().vrid == VRID

    async def test_a_reconciled_type_resumes_as_a_one_to_one_update(self) -> None:
        ep, processor, _gp, event = _setup(mime=MimeTypes.PDF.value, event_type="reindexRecord")

        await _drain(ep.on_event(event))

        assert processor.resume_from_checkpoint.call_args.args == (
            "rec-1", processor.load_checkpoint.return_value, VRID, "reindexRecord", VRID,
        )

    async def test_the_prior_marker_travels_with_the_attempt_to_the_vector_store(self) -> None:
        ep, _processor, _gp, event = _setup()

        await _drain(ep.on_event(event))

        attempt = current_attempt()
        assert attempt.prior["embedDeliveryId"] == "d1" and attempt.prior["embedDigest"] == "dig"
        assert attempt.rev == REV and attempt.delivery_id == "d1"


@pytest.mark.asyncio
class TestNoResume:
    @pytest.mark.parametrize(
        ("node", "kwargs"),
        [
            ({"embedDeliveryId": "d0"}, {}),                       # another event
            ({"embedRev": "other"}, {}),                           # other bytes
            ({"embedDigest": None}, {}),                           # died before it knew what to write
            ({"embedVrid": "vr-9"}, {}),                           # a marker for a vrid the node no longer holds
            ({}, {"force": True}),                                 # a user's reindex
            ({}, {"delivery_id": None}),                           # a consumer that gave no identity
            (dict.fromkeys(EMBED_FIELDS), {}),                 # no marker: a COMPLETED record queued again
        ],
        ids=["other-event", "other-rev", "no-digest", "other-vrid", "forced", "no-identity", "no-marker"],
    )
    async def test_the_event_is_processed_in_full(self, node: dict[str, Any], kwargs: dict[str, Any]) -> None:
        # Kills: reuse decided from status, from the doc, or ignoring any of the five conditions.
        ep, processor, _gp, event = _setup(node, **kwargs)

        await _drain(ep.on_event(event))

        processor.load_checkpoint.assert_not_awaited()
        processor.resume_from_checkpoint.assert_not_called()
        processor.process_delimited_document.assert_called_once()
        assert current_attempt() is None or not current_attempt().resume

    async def test_an_update_of_a_record_with_a_stale_marker_gets_a_new_vrid_and_cleans_the_old(self) -> None:
        # The interrupted run's points are removed the way an abandoned vrid's are.
        ep, _processor, _gp, event = _setup({"embedDeliveryId": "d0"}, event_type="updateRecord")

        await _drain(ep.on_event(event))

        ep._spawn_abandoned_vrid_cleanup.assert_called_once_with("org-1", VRID)
        ep._rewrite_or_delete_vrid_vectors.assert_awaited_once_with(VRID)

    async def test_a_missing_or_damaged_checkpoint_falls_back_to_the_full_path(self) -> None:
        ep, processor, _gp, event = _setup()
        processor.load_checkpoint = AsyncMock(return_value=None)

        await _drain(ep.on_event(event))

        processor.resume_from_checkpoint.assert_not_called()
        processor.process_delimited_document.assert_called_once()
        assert not current_attempt().resume

    @pytest.mark.parametrize("mime", [MimeTypes.SQL_TABLE.value])
    async def test_kinds_whose_stored_blocks_are_not_the_whole_content_never_resume(self, mime: str) -> None:
        ep, processor, _gp, event = _setup(mime=mime)
        processor.process_sql_table = MagicMock(side_effect=_mock_processor_gen)

        with contextlib.suppress(Exception):  # the SQL branch needs more of a record than this test builds
            await _drain(ep.on_event(event))

        processor.load_checkpoint.assert_not_awaited()
        assert current_attempt() is None


@pytest.mark.asyncio
class TestTwinFoundWhileResuming:
    async def test_the_marker_is_cleared_and_the_orphaned_points_are_removed(self) -> None:
        # Kills: a leaked marker naming a vrid the node no longer holds.
        ep, processor, gp, event = _setup()

        async def attach(file_content, doc, **_kw):  # noqa: ANN202
            doc["virtualRecordId"] = "vr-twin"
            return DedupDecision(skip_indexing=True)

        ep._check_duplicate_by_md5 = AsyncMock(side_effect=attach)
        with patch("app.events.events.notify_record_indexed", AsyncMock()):
            await _drain(ep.on_event(event))

        writes = [call.args[2] for call in gp.update_node.await_args_list]
        assert dict.fromkeys(EMBED_FIELDS) in writes
        ep._spawn_abandoned_vrid_cleanup.assert_called_once_with("org-1", VRID)
        ep._rewrite_or_delete_vrid_vectors.assert_awaited_once_with(VRID)
        processor.resume_from_checkpoint.assert_not_called()


@pytest.mark.asyncio
class TestAMarkerDoesNotOutliveItsEvent:
    async def test_a_run_that_cannot_resume_clears_the_marker_when_it_starts(self) -> None:
        # Kills: a marker left behind by an earlier cut event on a kind that never embeds under one.
        ep, _processor, gp, event = _setup(delivery_id=None)

        await _drain(ep.on_event(event))

        writes = [call.args[2] for call in gp.update_node.await_args_list]
        assert dict.fromkeys(EMBED_FIELDS) in writes

    async def test_a_run_with_no_marker_writes_no_clear(self) -> None:
        ep, _processor, gp, event = _setup(dict.fromkeys(EMBED_FIELDS), delivery_id=None)

        await _drain(ep.on_event(event))

        assert not any("embedVrid" in call.args[2] for call in gp.update_node.await_args_list)

    @pytest.mark.parametrize("status", ["COMPLETED", "EMPTY", "FAILED", "PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED"])
    def test_every_terminal_status_clears_it(self, status: str) -> None:
        assert chain_end_fields(status) == dict.fromkeys(EMBED_FIELDS)

    @pytest.mark.parametrize("status", ["QUEUED", "IN_PROGRESS", "NOT_STARTED", None])
    def test_a_status_a_retry_follows_keeps_it(self, status: str | None) -> None:
        assert chain_end_fields(status) == {}

    async def test_the_events_own_terminal_status_write_clears_it(self) -> None:
        ep, _processor, gp, _event = _setup()
        doc = {"_key": "rec-1"}

        await ep.mark_record_status(doc, ProgressStatus.FILE_TYPE_NOT_SUPPORTED)
        await ep.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

        terminal, running = (call.args[2] for call in gp.update_node.await_args_list)
        assert dict.fromkeys(EMBED_FIELDS).items() <= terminal.items()
        assert not set(EMBED_FIELDS) & set(running)

    @pytest.mark.parametrize(("status", "clears"), [("FAILED", True), ("PASSWORD_PROTECTED", True), ("QUEUED", False)])
    async def test_the_handlers_status_write_clears_it_only_when_the_chain_ends(self, status: str, clears: bool) -> None:
        from tests.unit.services.messaging.test_record_handler import _make_handler

        handler = _make_handler()
        graph = handler.event_processor.graph_provider
        graph.get_document = AsyncMock(return_value={"_key": "rec-1", "extractionStatus": "NOT_STARTED"})
        graph.update_node = AsyncMock(return_value=True)

        await handler._RecordEventHandler__update_document_status("rec-1", status, "NOT_STARTED")

        written = graph.update_node.await_args.args[2]
        assert (dict.fromkeys(EMBED_FIELDS).items() <= written.items()) is clears
        assert clears or not set(EMBED_FIELDS) & set(written)
