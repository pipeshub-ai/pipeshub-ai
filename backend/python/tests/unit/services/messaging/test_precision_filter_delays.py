"""The real filter: which events it hands back, and how long each waits."""

import logging
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.messaging.precision_check import (
    HANDBACKS_KEY,
    PrecisionFilter,
    PrecisionVerdict,
    Verdict,
)

log = logging.getLogger("filter-delays")


def _filter(mismatch: bool) -> PrecisionFilter:
    verdict = PrecisionVerdict(fetch_states=AsyncMock(), indexes=AsyncMock(), logger=log)
    verdict._verdict = Verdict(mismatch, ["x"] if mismatch else [])
    return PrecisionFilter(verdict, log, rng=random.Random(7))


def _message(event: str, **payload):  # noqa: ANN202
    return SimpleNamespace(eventType=event, payload=payload)


def test_the_first_hand_back_waits_a_minute_and_later_ones_ten() -> None:
    delays = _filter(True).hand_back([_message("newRecord"), _message("newRecord", **{HANDBACKS_KEY: 1})])
    assert 48 <= delays[0] <= 72
    assert 480 <= delays[1] <= 720


def test_only_the_events_that_write_vectors_are_handed_back() -> None:
    messages = [_message(e) for e in ("newRecord", "updateRecord", "reindexRecord", "deleteRecord", "deleteVectorCollection", "syncCompleted")]
    assert sorted(_filter(True).hand_back(messages)) == [0, 1, 2]


def test_nothing_is_handed_back_without_a_mismatch() -> None:
    assert _filter(False).hand_back([_message("newRecord")]) == {}
