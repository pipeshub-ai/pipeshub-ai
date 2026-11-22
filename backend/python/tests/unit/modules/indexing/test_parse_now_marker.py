"""The parse-now marker: how a record's status writes become marker fields during a run."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import ANY

import pytest

from app.modules.indexing import parse_now_marker as marker
from app.modules.indexing.parse_now_marker import (
    ParseNowRun,
    ParseNowStatus,
    begin_run,
    end_run,
    run_settings,
    shape_status_write,
    store_only_active,
)
from app.modules.transformers.transformer import PARSE_NOW, STORE_ONLY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REV = "rev-1"


@pytest.fixture(autouse=True)
def _no_run() -> Iterator[None]:
    end_run()
    yield
    end_run()


def test_outside_a_run_nothing_changes() -> None:
    fields = {"indexingStatus": "FAILED", "reason": "x"}

    assert shape_status_write(fields) == fields
    assert run_settings() == {}
    assert not store_only_active()


class TestStoreOnlyRun:
    @pytest.fixture(autouse=True)
    def _run(self) -> None:
        begin_run(ParseNowRun(store_only=True, content_rev=REV))

    def test_settings_name_the_run(self) -> None:
        assert run_settings() == {PARSE_NOW: True, STORE_ONLY: True}

    def test_status_fields_are_dropped_and_the_marker_written(self) -> None:
        shaped = shape_status_write({
            "indexingStatus": "IN_PROGRESS",
            "parsingStatus": "IN_PROGRESS",
            "processingStartedAt": 5,
            "contentRev": "rev-2",
        })

        assert shaped == {"contentRev": "rev-2", "parseNowStatus": "IN_PROGRESS", "parseNowRev": "rev-2", "parseNowReason": None, "parseNowAt": ANY}

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("EMPTY", ParseNowStatus.EMPTY),
            ("FAILED", ParseNowStatus.FAILED),
            ("PASSWORD_PROTECTED", ParseNowStatus.PASSWORD_PROTECTED),
            ("FILE_TYPE_NOT_SUPPORTED", ParseNowStatus.FILE_TYPE_NOT_SUPPORTED),
            ("ENABLE_MULTIMODAL_MODELS", ParseNowStatus.NEEDS_MULTIMODAL),
            ("QUEUED", ParseNowStatus.QUEUED),
            ("NOT_STARTED", ParseNowStatus.QUEUED),
        ],
    )
    def test_a_status_maps_to_its_marker_with_the_reason(self, status: str, expected: ParseNowStatus) -> None:
        shaped = shape_status_write({"indexingStatus": status, "extractionStatus": "FAILED", "reason": "why"})

        assert shaped == {"parseNowStatus": expected.value, "parseNowRev": REV, "parseNowReason": "why", "parseNowAt": ANY}

    @pytest.mark.parametrize("status", ["COMPLETED", "AUTO_INDEX_OFF", "SKIPPED"])
    def test_a_status_that_says_nothing_about_the_parse_writes_nothing(self, status: str) -> None:
        assert shape_status_write({"indexingStatus": status, "hasIndexedCopy": True}) == {}

    def test_a_clearing_write_and_a_parse_status_alone_write_nothing(self) -> None:
        # B7: the `reason` clear that opens a run must not erase why the record is AUTO_INDEX_OFF.
        assert shape_status_write({"reason": None}) == {}
        assert shape_status_write({"parsingStatus": "COMPLETED"}) == {}

    def test_other_fields_pass_through(self) -> None:
        assert shape_status_write({"virtualRecordId": "vr", "secretsRedacted": 0}) == {"virtualRecordId": "vr", "secretsRedacted": 0}


class TestIndexingRun:
    @pytest.fixture(autouse=True)
    def _run(self) -> None:
        begin_run(ParseNowRun(store_only=False, content_rev=REV))

    def test_settings_name_the_run_without_store_only(self) -> None:
        assert run_settings() == {PARSE_NOW: True}

    def test_the_status_is_kept_and_the_marker_added(self) -> None:
        shaped = shape_status_write({"indexingStatus": "FAILED", "extractionStatus": "FAILED", "reason": "why"})

        assert shaped["indexingStatus"] == "FAILED"
        assert shaped["parseNowStatus"] == "FAILED"
        assert shaped["parseNowReason"] == "why"


async def test_the_run_lives_in_the_task_that_began_it() -> None:
    """The handler, event processor, processor, pipeline and sink all run inside one task, and the
    variable they read is set by an async generator they iterate: it must reach the code that runs
    after the generator yields, and must not leak into another task."""
    seen: list[bool] = []

    async def handler() -> AsyncIterator[str]:
        begin_run(ParseNowRun(store_only=True))
        yield "first"
        seen.append(store_only_active())
        yield "second"

    async def chain() -> None:
        seen.extend([store_only_active() async for _ in handler()])

    async def other() -> bool:
        return store_only_active()

    await asyncio.create_task(chain())
    assert seen == [True, True, True]
    assert await asyncio.create_task(other()) is False
    assert marker.current_run() is None


async def test_a_created_task_inherits_the_run() -> None:
    begin_run(ParseNowRun(store_only=True))

    async def read() -> bool:
        return store_only_active()

    assert await asyncio.create_task(read()) is True
