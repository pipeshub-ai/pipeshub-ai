"""A parse the service refuses with 410 is never taken at its word: the consumer reads the record first."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.paid_cache.locks import DELETED_TTL_MS, InProcessLocks, deleted_name
from app.services.paid_cache.orphan import (
    MAX_REPARSES,
    DeliveryGuard,
    ProbeResult,
    RecordGone,
    RecordHandBack,
    bind_guard,
)
from app.services.parsing.client import ParsingClient, ParsingClientError
from app.services.parsing.interface import ParseErrorCode, ParseResult, ParserProvider

ORG, REC = "org-1", "rec-1"


def _gone() -> ParsingClientError:
    return ParsingClientError(code=ParseErrorCode.RECORD_GONE, message="the delete marker changed")


def _ok() -> ParseResult:
    from app.models.blocks import BlocksContainer

    return ParseResult(block_container=BlocksContainer(blocks=[], block_groups=[]), provider_used=ParserProvider.DEFAULT, metadata={})


class Rig:
    def __init__(self, replies: list[Any], probe: Any, *, wm: str | None = None) -> None:  # noqa: ANN401
        self.client = object.__new__(ParsingClient)
        self.locks = InProcessLocks()
        self.sent: list[dict[str, str]] = []
        self.replies = list(replies)

        async def once(*_args: Any, extra_fields: dict[str, str], **_kw: Any) -> ParseResult:  # noqa: ANN401
            self.sent.append(dict(extra_fields))
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.client._parse_once = once  # type: ignore[method-assign]
        self.guard = DeliveryGuard(org_id=ORG, record_id=REC, wm_record=wm, created=1, probe=probe, backend_of=self._backend)
        bind_guard(self.guard)

    async def _backend(self) -> InProcessLocks:
        return self.locks

    async def parse(self) -> ParseResult:
        try:
            return await self.client.parse(b"x", "a.pdf", "application/pdf", "pdf", ORG)
        finally:
            self.guard.finish()


async def test_a_410_whose_record_is_gone_drains_and_is_not_parsed_again() -> None:
    rig = Rig([_gone()], AsyncMock(return_value=ProbeResult(found=False)))
    with pytest.raises(RecordGone):
        await rig.parse()
    assert len(rig.sent) == 1 and rig.guard.gone


async def test_a_410_whose_confirming_read_fails_is_handed_back_never_drained_and_never_parsed_again_at_once() -> None:
    rig = Rig([_gone()], AsyncMock(side_effect=ConnectionError("graph down")))
    with pytest.raises(RecordHandBack) as info:
        await rig.parse()
    assert len(rig.sent) == 1 and rig.guard.hand_back and not rig.guard.gone
    assert info.value.delay_s and info.value.delay_s > 0


async def test_a_410_for_a_live_record_is_parsed_again_with_the_markers_just_read_at_most_twice_then_with_the_stop_disabled() -> None:
    rig = Rig([_gone(), _gone(), _gone(), _ok()], AsyncMock(return_value=ProbeResult(True, True, 1)), wm="100")
    await rig.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "200")                 # the redelivered old delete
    result = await rig.parse()
    assert result is not None
    assert [s["record_marker"] for s in rig.sent] == ["100", "200", "200", "200"]       # the new watermark, sent from the 2nd on
    assert [("stop_disabled" in s) for s in rig.sent] == [False, False, False, True]    # the stop is off for the last one
    assert rig.guard.reparses == MAX_REPARSES + 1


async def test_a_410_for_a_newer_incarnation_drains_the_old_run() -> None:
    rig = Rig([_gone()], AsyncMock(return_value=ProbeResult(True, True, created=2)))
    with pytest.raises(RecordGone):
        await rig.parse()


async def test_a_parse_with_no_guard_or_another_error_is_unchanged() -> None:
    rig = Rig([ParsingClientError(code=ParseErrorCode.PARSE_FAILED, message="boom")], AsyncMock())
    with pytest.raises(ParsingClientError):
        await rig.parse()
    assert rig.guard.reparses == 0 and not rig.guard.hand_back


async def test_a_410_whose_confirming_read_hangs_is_handed_back_within_the_probe_bound() -> None:
    async def hang() -> ProbeResult:
        await asyncio.sleep(3600)
        return ProbeResult(True, True, 1)

    rig = Rig([_gone()], hang)
    rig.guard.probe_timeout_s = 0.2
    with pytest.raises(RecordHandBack):
        await asyncio.wait_for(rig.parse(), 2.0)
    assert len(rig.sent) == 1 and rig.guard.hand_back and not rig.guard.gone


async def test_after_the_reparse_cap_the_consumers_own_checks_still_stop_a_real_delete() -> None:
    """Design 3.3: ``stopDisabled`` disables the parsing service's stop only; the consumer's checks and the fence remain."""
    from app.services.paid_cache.orphan import ensure_record_alive

    state = {"live": True}

    async def probe() -> ProbeResult:
        return ProbeResult(True, True, 1) if state["live"] else ProbeResult(found=False)

    rig = Rig([_gone(), _gone(), _gone(), _ok()], probe, wm="100")
    await rig.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "200")
    rig.guard.closed = False
    await rig.client.parse(b"x", "a.pdf", "application/pdf", "pdf", ORG)        # not finished: the guard is still bound
    assert rig.guard.reparses == MAX_REPARSES + 1 and rig.sent[-1].get("stop_disabled") == "true"
    state["live"] = False                                                       # now a real delete of this record
    await rig.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "300")
    rig.guard.clock = lambda: 10_000.0
    with pytest.raises(RecordGone):
        await ensure_record_alive(ORG, REC, "embed")
