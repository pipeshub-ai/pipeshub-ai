"""Replays a measured two-connector indexing run through the real scheduler.

A slow connector (GitHub pull requests) and a fast one (Slack messages)
index side by side into one pool of 15 light index slots. This drives the
real ``DRRScheduler`` and ``SlotShare`` through a small discrete-event model
of the dispatch loop and the slot gate, so it shows what the dispatcher
does with these record costs, without timing noise or a live service.

Where the numbers come from (a real overnight indexing run, measured from
its logs and the per-record timing lines added in #4044):

* The light index pool sat at 15 slots for the whole run.
* With GitHub paused, Slack indexed about 51 records a minute on 15 slots,
  so a Slack record holds its slot for about 15 * 60 / 51 = 17.6 s.
* In the intervals where both connectors had a backlog, they completed
  about the same *number* of records (4.7 vs 4.9, 3.0 vs 2.7, 2.1 vs 3.4 a
  minute), and Slack ran at 2.7 to 4.9 a minute. GitHub records therefore
  held their slots for about 256 s on average.
* Record sizes are a sample of 20 real pull requests: the text of their
  diffs, and of the full changed files that PR records carried until #4140.
  A record's hold time is modelled as Slack's fixed cost plus a cost per
  byte, calibrated so the old sizes average 256 s. With #4140's smaller
  records the same model gives about 38 s.

The model is deliberately simple: one node, one pool at a fixed limit, an
unlimited backlog for both connectors, and hold times that do not depend on
how busy the pool is. It is a check on the dispatcher, not a forecast.
"""
from __future__ import annotations

import heapq
import itertools
import math
from collections import Counter, deque
from dataclasses import dataclass, field

import pytest

from app.services.messaging.scheduling.drr_scheduler import DRRScheduler
from app.services.messaging.scheduling.interface import FairSchedulerConfig
from app.services.messaging.scheduling.slot_share import SlotShare

SLOTS = 15
# consumer_concurrency.pending_task_ceiling at this limit: max(64, 2 * 30).
# The light tier's dispatch budget is the whole total, so this many records
# can be dispatched and queued at the gate ahead of a free slot.
PENDING_CEILING = 64
SLACK_HOLD_S = 17.6
GITHUB_MEAN_HOLD_BEFORE_S = 256.0

# (diff characters, full changed-file bytes) for 20 real pull requests.
PR_SIZES = [
    (1680, 64431), (16700, 686293), (1615, 45055), (14333, 94441),
    (343, 4198), (5631, 640046), (49258, 96346), (53931, 741532),
    (36132, 129849), (47727, 2593570), (198767, 1342122), (8831, 15980),
    (2048, 2743), (27291, 310891), (1318, 17171), (205128, 271555),
    (16967, 98232), (3759, 0), (37491, 358887), (908, 110014),
]
_MEAN_BEFORE_BYTES = sum(diff + full for diff, full in PR_SIZES) / len(PR_SIZES)
_SECONDS_PER_BYTE = (GITHUB_MEAN_HOLD_BEFORE_S - SLACK_HOLD_S) / _MEAN_BEFORE_BYTES
GITHUB_HOLDS_BEFORE = [
    SLACK_HOLD_S + _SECONDS_PER_BYTE * (diff + full) for diff, full in PR_SIZES
]
GITHUB_HOLDS_TODAY = [SLACK_HOLD_S + _SECONDS_PER_BYTE * diff for diff, _ in PR_SIZES]

SLACK = ("org", "slack", "light")
GITHUB = ("org", "github", "light")


@dataclass
class Result:
    completed: Counter = field(default_factory=Counter)
    slot_seconds: Counter = field(default_factory=Counter)
    minutes: float = 0.0
    max_in_flight: Counter = field(default_factory=Counter)

    def per_minute(self, key: tuple[str, ...]) -> float:
        return self.completed[key] / self.minutes

    def slot_share(self, key: tuple[str, ...]) -> float:
        total = sum(self.slot_seconds.values())
        return self.slot_seconds[key] / total if total else 0.0


def replay(
    holds: dict[tuple[str, ...], list[float]],
    *,
    share: bool,
    hours: float = 2.0,
    warmup_hours: float = 0.25,
) -> Result:
    """Run the dispatcher against unlimited backlogs with the given hold
    times (cycled in order), and report what completed after the warmup."""
    config = FairSchedulerConfig(
        enabled=True,
        key_fields=("orgId", "connectorId"),
        max_buffered_messages=2000,
        max_per_entity_messages=500,
    )
    scheduler: DRRScheduler[int] = DRRScheduler(config)
    slot_share = SlotShare(config.entity_depth) if share else None
    next_hold = {key: itertools.cycle(values) for key, values in holds.items()}
    record_ids = itertools.count()
    hold_of: dict[int, float] = {}
    key_of: dict[int, tuple[str, ...]] = {}

    def refill() -> None:
        # The read phase keeps every connector's queue topped up.
        for key in holds:
            while scheduler.pending_count_for(key[:2]) < config.max_per_entity_messages:
                record = next(record_ids)
                hold_of[record] = next(next_hold[key])
                key_of[record] = key
                scheduler.enqueue(key, record)

    gate: deque[int] = deque()  # dispatched, waiting for a slot (FIFO)
    in_use = 0
    events: list[tuple[float, int]] = []  # (finish time, record)
    in_flight: Counter = Counter()
    result = Result()
    end = hours * 3600
    warmup = warmup_hours * 3600

    def pool_of(_key: tuple[str, ...]) -> str:
        return "index_light"

    def dispatch() -> None:
        # The consumer's dispatch phase: spawn tasks while the tier's
        # pending budget has room, in DRR order, honouring the share.
        while len(gate) < PENDING_CEILING:
            plan = (
                slot_share.plan(scheduler.backlogged_keys(), pool_of, lambda _p: SLOTS)
                if slot_share is not None
                else None
            )
            popped = scheduler.dequeue(
                key_allowed=plan.allows if plan is not None else None
            )
            if popped is None:
                return
            key, record = popped
            if slot_share is not None:
                slot_share.started(record, key, "index_light")
            in_flight[key] += 1
            result.max_in_flight[key] = max(result.max_in_flight[key], in_flight[key])
            gate.append(record)
            refill()

    def admit(now: float) -> None:
        nonlocal in_use
        while gate and in_use < SLOTS:
            record = gate.popleft()
            in_use += 1
            heapq.heappush(events, (now + hold_of[record], record))

    refill()
    dispatch()
    admit(0.0)
    while events:
        now, record = heapq.heappop(events)
        if now > end:
            break
        key = key_of.pop(record)
        hold = hold_of.pop(record)
        in_use -= 1
        in_flight[key] -= 1
        if slot_share is not None:
            slot_share.finished(record)
        if now - hold >= warmup:
            result.completed[key] += 1
            result.slot_seconds[key] += hold
        dispatch()
        admit(now)
    result.minutes = (end - warmup) / 60
    return result


def _solo(holds: list[float], key: tuple[str, ...], share: bool) -> Result:
    return replay({key: holds}, share=share)


class TestModelMatchesTheMeasuredRun:
    """Without the share the model reproduces what the logs showed, so its
    other answers are worth something."""

    def test_slack_alone_runs_at_the_measured_rate(self) -> None:
        result = _solo([SLACK_HOLD_S], SLACK, share=False)
        assert result.per_minute(SLACK) == pytest.approx(51, rel=0.02)

    def test_counting_records_gives_github_nearly_every_slot(self) -> None:
        result = replay(
            {GITHUB: GITHUB_HOLDS_BEFORE, SLACK: [SLACK_HOLD_S]}, share=False
        )
        # Equal counts, as measured...
        assert result.per_minute(GITHUB) == pytest.approx(
            result.per_minute(SLACK), rel=0.05
        )
        # ...so Slack sits in the measured 2.7-4.9 a minute, and GitHub
        # holds over 90% of the slot time.
        assert 2.7 <= result.per_minute(SLACK) <= 4.9
        assert result.slot_share(GITHUB) > 0.9


class TestSharingSlotTime:
    @pytest.mark.parametrize(
        "github_holds",
        [GITHUB_HOLDS_BEFORE, GITHUB_HOLDS_TODAY],
        ids=["records-before-4140", "records-today"],
    )
    def test_slow_connector_is_held_to_its_share_while_the_other_waits(
        self, github_holds
    ) -> None:
        result = replay({GITHUB: github_holds, SLACK: [SLACK_HOLD_S]}, share=True)

        cap = math.ceil(SLOTS / 2)
        assert result.max_in_flight[GITHUB] <= cap
        # About half the slot time each, rather than whatever the cost ratio
        # of their records happens to be.
        assert result.slot_share(GITHUB) <= cap / SLOTS + 0.01
        # Slack keeps roughly half its solo rate: 7 of 15 slots at 17.6 s.
        assert result.per_minute(SLACK) >= 0.9 * (SLOTS - cap) * 60 / SLACK_HOLD_S

    def test_slack_gains_with_todays_record_sizes_too(self) -> None:
        before = replay(
            {GITHUB: GITHUB_HOLDS_TODAY, SLACK: [SLACK_HOLD_S]}, share=False
        )
        after = replay({GITHUB: GITHUB_HOLDS_TODAY, SLACK: [SLACK_HOLD_S]}, share=True)
        # Smaller PR records already narrowed the gap; the share still
        # moves Slack from about 16 to about 25 records a minute.
        assert after.per_minute(SLACK) > 1.4 * before.per_minute(SLACK)

    @pytest.mark.parametrize("share", [False, True])
    def test_a_connector_alone_keeps_every_slot(self, share) -> None:
        result = _solo(GITHUB_HOLDS_BEFORE, GITHUB, share=share)
        mean_hold = sum(GITHUB_HOLDS_BEFORE) / len(GITHUB_HOLDS_BEFORE)
        assert result.per_minute(GITHUB) == pytest.approx(
            SLOTS * 60 / mean_hold, rel=0.1
        )


def summary() -> list[tuple[str, str, float, float, float]]:
    """(record sizes, dispatcher, Slack/min, GitHub/min, GitHub slot share)
    for the PR description. Not a test."""
    rows = []
    for label, holds in (
        ("before #4140", GITHUB_HOLDS_BEFORE),
        ("today", GITHUB_HOLDS_TODAY),
    ):
        for share in (False, True):
            result = replay({GITHUB: holds, SLACK: [SLACK_HOLD_S]}, share=share)
            rows.append(
                (
                    label,
                    "share" if share else "count",
                    result.per_minute(SLACK),
                    result.per_minute(GITHUB),
                    result.slot_share(GITHUB),
                )
            )
    return rows

