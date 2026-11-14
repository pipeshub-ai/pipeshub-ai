"""A flip inside a record goes back to the broker; it never fails the record, not even on its last delivery."""

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError
from app.services.messaging.precision_flip import PrecisionFlip
from tests.unit.services.messaging.test_record_handler_parse_now import (
    Harness,
    _payload,
    _record,
)


def _wrapped_flip() -> Exception:
    """The flip as the pipeline's wrappers leave it: two errors above it."""
    inner = RuntimeError("Failed to store batch 0")
    inner.__cause__ = PrecisionFlip("collection 'records' holds int8-w and the server now produces fp32")
    outer = IndexingError("Failed to get embedding model instance")
    outer.__cause__ = inner
    return outer


@pytest.mark.asyncio
class TestFlip:
    async def _run(self, error: Exception, *, final: bool) -> Harness:
        harness = Harness(_record(indexingStatus="IN_PROGRESS"))
        harness.fail_with = error
        with pytest.raises(Exception) as caught:
            await harness.run(_payload(parseNow=False, is_final_failure=final), EventTypes.NEW_RECORD.value)
        harness.caught = caught.value
        return harness

    async def test_a_flip_below_the_wrappers_reaches_the_consumer_as_a_flip(self) -> None:
        harness = await self._run(_wrapped_flip(), final=False)
        assert isinstance(harness.caught, PrecisionFlip)
        assert harness.caught.counts_toward_backstop is False and harness.caught.pauses_consumer is False

    async def test_a_flip_on_the_last_delivery_is_never_written_as_failed(self) -> None:
        harness = await self._run(_wrapped_flip(), final=True)
        assert isinstance(harness.caught, PrecisionFlip)
        assert all(write.get("indexingStatus") != "FAILED" for write in harness.writes())
        harness.handler._propagate_primary_failure_to_queued_duplicates.assert_not_awaited()

    async def test_an_ordinary_error_on_the_last_delivery_still_fails_the_record(self) -> None:
        harness = await self._run(IndexingError("embedding exploded"), final=True)
        assert any(write.get("indexingStatus") == "FAILED" for write in harness.writes())
