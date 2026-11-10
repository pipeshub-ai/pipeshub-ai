"""What ``on_event`` tells the consumer's progress object, and the one write that hands a cut record back."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.modules.indexing.embed_resume import end_attempt
from app.modules.indexing.record_progress import (
    RecordProgress,
    StallHistory,
    begin_progress,
    end_progress,
)
from tests.unit.events.test_events import _drain
from tests.unit.events.test_events_resume import _setup

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    end_attempt()
    end_progress()
    yield
    end_attempt()
    end_progress()


def _progress() -> RecordProgress:
    return RecordProgress(floor_seconds=1, ceiling_seconds=2, record_ceiling_seconds=100, history=StallHistory())


@pytest.mark.asyncio
class TestBinding:
    async def test_the_clock_of_the_chain_is_read_from_the_node_for_the_same_event(self) -> None:
        # Kills: counters started from zero on every attempt, which would reset the 6 h ceiling.
        ep, _p, _gp, event = _setup({"embedSeconds": 700.0, "embedUncounted": 3})
        progress = _progress()
        begin_progress(progress)
        await _drain(ep.on_event(event))
        assert (progress.prior_seconds, progress.prior_uncounted, progress.delivery_id) == (700.0, 3, "d1")

    async def test_a_new_event_starts_a_new_clock(self) -> None:
        ep, _p, _gp, event = _setup({"embedSeconds": 700.0, "embedUncounted": 3, "embedDeliveryId": "older"})
        progress = _progress()
        begin_progress(progress)
        await _drain(ep.on_event(event))
        assert (progress.prior_seconds, progress.prior_uncounted) == (0.0, 0)

    async def test_a_marker_without_a_clock_is_unknown(self) -> None:
        # Kills: a marker from before the clock existed being given a fresh 6 h.
        ep, _p, _gp, event = _setup({"embedSeconds": None, "embedUncounted": None})
        progress = _progress()
        begin_progress(progress)
        await _drain(ep.on_event(event))
        assert progress.prior_seconds is None

    async def test_a_run_without_progress_is_not_affected(self) -> None:
        ep, processor, _gp, event = _setup()
        await _drain(ep.on_event(event))
        processor.resume_from_checkpoint.assert_called_once()


@pytest.mark.asyncio
class TestTheHandBackWrite:
    async def _writer(self, node: dict[str, Any] | None = None, *, applies: bool = True) -> tuple[Any, Any]:
        ep, _p, gp, _event = _setup(node)
        gp.update_node_fields_if_match = AsyncMock(return_value=applies)
        return ep._cut_writer("rec-1", "d1"), gp

    async def test_the_progress_count_is_written_only_while_this_delivery_holds_the_record(self) -> None:
        # Kills: an unconditional count, which a late attempt would put on a record that finished.
        ep, _p, gp, _event = _setup({})
        gp.update_node_fields_if_match = AsyncMock(return_value=True)
        await ep._display_writer("rec-1", "d1")(340)
        _key, _coll, updates, expected = gp.update_node_fields_if_match.await_args.args
        assert updates == {"embedDone": 340}
        assert expected == {"embedDeliveryId": "d1", "indexingStatus": "IN_PROGRESS"}

    async def test_it_is_conditional_on_this_delivery_and_the_record_still_being_in_progress(self) -> None:
        # Kills: an unconditional status write, which would overwrite a record another owner took over.
        write, gp = await self._writer({"parsingStatus": "IN_PROGRESS"})
        assert await write(123.5, 4) is True
        _key, _coll, updates, expected = gp.update_node_fields_if_match.await_args.args
        assert expected == {"embedDeliveryId": "d1", "indexingStatus": "IN_PROGRESS"}
        assert updates["indexingStatus"] == "QUEUED"
        assert updates["embedSeconds"] == 123.5 and updates["embedUncounted"] == 4
        assert updates["processingStartedAt"] is None and updates["queuedAtTimestamp"]
        assert updates["parsingStatus"] == "NOT_STARTED"
        gp.update_node.assert_not_awaited()

    async def test_an_unreadable_record_is_not_written_over_as_not_started(self) -> None:
        # Kills: a failed read that looks like an empty record, which would reset a COMPLETED extraction.
        write, gp = await self._writer({"extractionStatus": "COMPLETED"})

        async def read(_key: str, _coll: str, **kw: Any) -> Any:  # noqa: ANN401
            if kw.get("raise_on_error"):
                raise RuntimeError("graph down")
            return {}  # what an unflagged read answers when the query fails

        gp.get_document = AsyncMock(side_effect=read)
        with pytest.raises(RuntimeError):
            await write(1.0, 1)
        gp.update_node_fields_if_match.assert_not_awaited()

    async def test_it_reports_a_refusal(self) -> None:
        write, _gp = await self._writer(applies=False)
        assert await write(1.0, 1) is False

    async def test_it_does_not_touch_the_marker_that_resume_needs(self) -> None:
        # Kills: clearing the marker on a hand-back, which would turn the requeue into a full restart.
        write, gp = await self._writer()
        await write(1.0, 1)
        updates = gp.update_node_fields_if_match.await_args.args[2]
        assert not {"embedVrid", "embedRev", "embedKey", "embedDigest", "embedDeliveryId"} & set(updates)
