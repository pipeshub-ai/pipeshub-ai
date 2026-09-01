"""Unit tests for the Kafka commit-watermark tracker."""
from __future__ import annotations

from unittest.mock import Mock

from aiokafka import TopicPartition

from app.services.messaging.scheduling.offset_tracker import PartitionOffsetTracker


def _tp(partition: int = 0) -> TopicPartition:
    return TopicPartition("record-events", partition)


class _FakeClock:
    """Monotonic clock the dwell tests drive by hand, so they assert the
    sweep's arithmetic instead of sleeping."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestContiguousProgress:
    def test_single_offset_done_advances_watermark(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 5)
        assert tracker.mark_done(tp, 5) == 6

    def test_sequential_in_order_offsets(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(3):
            tracker.track(tp, offset)
        assert tracker.mark_done(tp, 0) == 1
        assert tracker.mark_done(tp, 1) == 2
        assert tracker.mark_done(tp, 2) == 3


class TestOutOfOrderCompletion:
    def test_higher_offset_done_first_does_not_advance_past_gap(self):
        """DRR can finish offset 2 before offset 0/1 -- the watermark must
        stay behind the still-outstanding lower offset, or a crash would
        lose it."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(3):
            tracker.track(tp, offset)

        # 0 and 1 are still outstanding, so the watermark can only cover up
        # to (not including) the lowest still-outstanding offset.
        assert tracker.mark_done(tp, 2) == 0
        # No further progress below 0/1: repeating it must not re-advance.
        assert tracker.mark_done(tp, 2) is None

    def test_gap_fills_and_watermark_jumps_to_cover_all_done(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(4):
            tracker.track(tp, offset)

        assert tracker.mark_done(tp, 2) == 0     # 0,1 still outstanding
        assert tracker.mark_done(tp, 3) is None  # 0,1 still outstanding, no new progress
        assert tracker.mark_done(tp, 0) == 1     # only 1 still outstanding
        result = tracker.mark_done(tp, 1)
        # Everything 0-3 is now done: watermark jumps straight to 4.
        assert result == 4

    def test_no_regression_once_watermark_advances(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(2):
            tracker.track(tp, offset)
        assert tracker.mark_done(tp, 0) == 1
        # Nothing new resolved for offset 0 again -- no double-advance/regression.
        tracker.track(tp, 2)
        assert tracker.mark_done(tp, 2) is None  # offset 1 still outstanding, watermark stuck at 1


class TestMultiplePartitionsAreIndependent:
    def test_watermarks_tracked_per_partition(self):
        tracker = PartitionOffsetTracker()
        tp0, tp1 = _tp(0), _tp(1)
        tracker.track(tp0, 0)
        tracker.track(tp1, 0)

        assert tracker.mark_done(tp0, 0) == 1
        # tp1's offset 0 is untouched by tp0's progress.
        assert tracker.mark_done(tp1, 0) == 1


class TestRevoke:
    def test_revoke_drops_all_state_for_partition(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 0)
        tracker.track(tp, 1)
        tracker.mark_done(tp, 0)

        tracker.revoke(tp)

        # Fresh start: tracking offset 5 next should behave as if this
        # partition was never seen before.
        tracker.track(tp, 5)
        assert tracker.mark_done(tp, 5) == 6

    def test_revoke_of_untracked_partition_is_noop(self):
        tracker = PartitionOffsetTracker()
        tracker.revoke(_tp())  # must not raise


class TestMarkDoneWithoutPriorTrack:
    def test_mark_done_without_track_still_advances(self):
        """Defensive: even if track() was skipped, mark_done must not raise
        and should still fold the offset into high-water-seen."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        assert tracker.mark_done(tp, 0) == 1

    def test_untracked_resolution_is_logged_as_an_error(self):
        """Resolving an offset the watermark never saw enter the buffer is
        how offsets get silently skipped -- it must be loud."""
        logger = Mock()
        tracker = PartitionOffsetTracker(logger=logger)
        tracker.mark_done(_tp(), 7)
        assert logger.error.called

    def test_tracked_resolution_logs_nothing(self):
        logger = Mock()
        tracker = PartitionOffsetTracker(logger=logger)
        tp = _tp()
        tracker.track(tp, 7)
        tracker.mark_done(tp, 7)
        assert not logger.error.called


class TestRedeliverFloor:
    """The regression suite for the pinned-watermark bug: paths that return
    without committing (record-lease contention, shutdown) must not silently
    stall every later commit on the partition."""

    def test_redeliver_floors_the_watermark_at_that_offset(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(5):
            tracker.track(tp, offset)

        assert tracker.mark_done(tp, 0) == 1
        # Offset 1 is abandoned: it was never processed, so the watermark
        # must stop at it rather than committing past unprocessed work.
        assert tracker.mark_redeliver(tp, 1) is None
        for offset in (2, 3, 4):
            assert tracker.mark_done(tp, offset) is None

    def test_redelivered_offset_clears_its_floor_when_re_tracked(self):
        """The floor is released by the redelivery itself, not by a timer:
        re-reading the offset re-tracks it, and finishing it then lets the
        watermark jump over everything resolved behind it."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(3):
            tracker.track(tp, offset)
        tracker.mark_done(tp, 0)
        tracker.mark_redeliver(tp, 1)
        tracker.mark_done(tp, 2)

        tracker.track(tp, 1)  # redelivered after a restart/rebalance
        assert tracker.mark_done(tp, 1) == 3

    def test_done_after_redeliver_does_not_leave_a_stale_floor(self):
        """A message abandoned and then re-read and completed in the same
        process must not keep its floor."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0)
        tracker.track(tp, 0)
        assert tracker.mark_done(tp, 0) == 1


class TestNoPinAcrossManyMessages:
    def test_lease_contention_resolved_as_done_keeps_commits_flowing(self):
        """The production path: a contended record lease means a duplicate
        delivery already owns the record, so this delivery is finished with.
        Resolving it as done (not redeliver) is what keeps the watermark
        moving -- the bug was leaving it unresolved entirely."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(100, 110):
            tracker.track(tp, offset)

        assert tracker.mark_done(tp, 100) == 101
        tracker.mark_done(tp, 101)  # contended duplicate: finished with
        for offset in range(102, 110):
            tracker.mark_done(tp, offset)

        # Every offset resolved, so the watermark cleared the whole batch.
        assert tracker.watermark_lag(tp) == 0

    def test_unresolved_offset_pins_the_watermark(self):
        """Guards the invariant the rest of the design rests on: if any
        offset is left unresolved, commits stop. The consumer's dwell sweep
        is the escape hatch for exactly this."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(100, 110):
            tracker.track(tp, offset)
        tracker.mark_done(tp, 100)
        # 101 deliberately left unresolved.
        for offset in range(102, 110):
            assert tracker.mark_done(tp, offset) is None
        assert tracker.watermark_lag(tp) > 0


class TestDwellSweep:
    def test_stale_offsets_reports_only_offsets_past_the_budget(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        clock.advance(30)
        tracker.track(tp, 1)
        tracker.mark_dispatched(tp, 1)

        stale = tracker.stale_offsets(max_dwell_seconds=20)
        assert [entry.offset for entry in stale] == [0]
        assert stale[0].age_seconds == 30

    def test_resolved_offsets_are_not_stale(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        tracker.mark_done(tp, 0)
        clock.advance(1000)
        assert tracker.stale_offsets(max_dwell_seconds=10) == []

    def test_redeliver_floors_also_age_out(self):
        """A floor from a broken-state path would otherwise pin the
        watermark until the process restarts."""
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0)
        clock.advance(1000)
        assert [entry.offset for entry in tracker.stale_offsets(10)] == [0]

    def test_a_redelivered_offset_keeps_its_dispatch_clock(self):
        """It was already being worked on, so the age that matters is how
        long since a worker took it -- not since it was handed back."""
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        clock.advance(50)
        tracker.mark_redeliver(tp, 0)
        clock.advance(10)
        assert tracker.stale_offsets(10)[0].age_seconds == 60

    def test_a_never_dispatched_offset_ages_from_when_it_was_handed_back(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        clock.advance(50)
        tracker.mark_redeliver(tp, 0)
        clock.advance(10)
        assert tracker.stale_offsets(5)[0].age_seconds == 10


class TestWatermarkLag:
    def test_lag_is_zero_for_an_unseen_partition(self):
        assert PartitionOffsetTracker().watermark_lag(_tp()) == 0

    def test_lag_grows_while_an_offset_is_outstanding(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(5):
            tracker.track(tp, offset)
        tracker.mark_done(tp, 0)
        assert tracker.watermark_lag(tp) == 4

    def test_revoke_clears_lag(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 9)
        tracker.revoke(tp)
        assert tracker.watermark_lag(tp) == 0


class TestWatermarkLagBeforeAnyCommit:
    """A partition that has read offsets but resolved none has never
    reported a watermark. Measuring lag against the last *reported* one
    showed zero there -- exactly the state the metric exists to surface."""

    def test_lag_counts_unresolved_offsets_before_the_first_commit(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(5):
            tracker.track(tp, offset)
        assert tracker.watermark_lag(tp) == 5

    def test_lag_shrinks_as_the_contiguous_prefix_resolves(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(5):
            tracker.track(tp, offset)
        tracker.mark_done(tp, 0)
        assert tracker.watermark_lag(tp) == 4
        tracker.mark_done(tp, 1)
        assert tracker.watermark_lag(tp) == 3

    def test_out_of_order_resolution_does_not_shrink_lag(self):
        """Resolving a high offset while a lower one is outstanding must not
        look like progress -- the watermark cannot pass the lower one."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(5):
            tracker.track(tp, offset)
        tracker.mark_done(tp, 4)
        assert tracker.watermark_lag(tp) == 5

    def test_a_redelivery_floor_holds_the_lag_up(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        for offset in range(3):
            tracker.track(tp, offset)
        tracker.mark_redeliver(tp, 0)
        tracker.mark_done(tp, 1)
        tracker.mark_done(tp, 2)
        assert tracker.watermark_lag(tp) == 3


class TestDwellAppliesOnlyToDispatchedWork:
    """The dwell sweep force-commits past an offset, so it must never fire
    for one that nothing has tried to process yet.

    Two states float the watermark without being stuck: an offset still
    buffered awaiting its turn, and one refused for want of buffer room and
    seeked back for re-reading. Committing past either skips a message
    outright.
    """

    def test_buffered_but_undispatched_offsets_never_go_stale(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        clock.advance(10_000)
        assert tracker.stale_offsets(max_dwell_seconds=1) == []

    def test_dispatched_offsets_do_go_stale(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        clock.advance(1_000)
        assert [s.offset for s in tracker.stale_offsets(10)] == [0]

    def test_dwell_clock_starts_at_dispatch_not_at_track(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        clock.advance(500)
        tracker.mark_dispatched(tp, 0)
        clock.advance(10)
        assert tracker.stale_offsets(100) == []
        clock.advance(200)
        assert [s.offset for s in tracker.stale_offsets(100)] == [0]

    def test_re_reading_an_offset_resets_its_dwell_state(self):
        """A refused offset is seeked back and re-read. That is a fresh
        delivery attempt and must not inherit the previous one's clock."""
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        clock.advance(1_000)
        assert tracker.stale_offsets(10)

        tracker.track(tp, 0)  # redelivered
        assert tracker.stale_offsets(10) == []

    def test_redelivery_floors_still_age_out(self):
        """Those genuinely will not come back on their own, so the escape
        hatch must still apply to them."""
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0)
        clock.advance(1_000)
        assert [s.offset for s in tracker.stale_offsets(10)] == [0]

    def test_mark_dispatched_for_an_unknown_offset_is_a_noop(self):
        tracker = PartitionOffsetTracker()
        tracker.mark_dispatched(_tp(), 99)  # must not raise
        assert tracker.stale_offsets(0) == []


class TestSweepActuallyUnpinsAFloor:
    """The dwell sweep resolves what it finds via mark_done. If that left the
    redelivery floor in place, the sweep would report an escape without
    making one -- and log a spurious untracked-offset error while doing it."""

    def test_mark_done_clears_a_redelivery_floor(self):
        logger = Mock()
        tracker = PartitionOffsetTracker(logger=logger)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.track(tp, 1)
        tracker.mark_dispatched(tp, 0)
        tracker.mark_redeliver(tp, 0)
        tracker.mark_done(tp, 1)
        assert tracker.watermark_lag(tp) > 0

        for stale in tracker.stale_offsets(0.0):
            tracker.mark_done(stale.tp, stale.offset)

        assert tracker.watermark_lag(tp) == 0
        assert not logger.error.called

    def test_clearing_a_floor_directly_does_not_resolve_the_offset(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0)
        tracker.clear_partition_floor(tp, 0)
        assert tracker.watermark_lag(tp) == 0


class TestFloorsAwaitingRereadAreNotSwept:
    """A floor means different things depending on whether the caller also
    rewound the partition.

    Rewound: the offset comes back on its own, so it is queued rather than
    stuck, and force-committing past it would skip a message nothing has
    processed. Not rewound: on Kafka the read position is already past it, so
    it only returns on a restart or rebalance and would otherwise pin the
    partition's commits until then -- the case the sweep exists for.
    """

    def test_a_rewound_floor_is_exempt_from_the_dwell_sweep(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0, awaiting_reread=True)
        clock.advance(10_000)

        assert tracker.stale_offsets(1) == []

    def test_a_rewound_floor_still_holds_the_watermark(self):
        """Exempt from the sweep is not the same as forgotten: nothing may
        commit past it before it is re-read."""
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 0)
        tracker.track(tp, 1)
        tracker.mark_redeliver(tp, 0, awaiting_reread=True)
        tracker.mark_done(tp, 1)

        assert tracker.watermark_lag(tp) == 2

    def test_a_floor_that_will_not_come_back_still_ages_out(self):
        clock = _FakeClock()
        tracker = PartitionOffsetTracker(clock=clock)
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_dispatched(tp, 0)
        tracker.mark_redeliver(tp, 0)
        clock.advance(10_000)

        assert [entry.offset for entry in tracker.stale_offsets(1)] == [0]

    def test_re_reading_clears_an_exempt_floor(self):
        tracker = PartitionOffsetTracker()
        tp = _tp()
        tracker.track(tp, 0)
        tracker.mark_redeliver(tp, 0, awaiting_reread=True)
        tracker.track(tp, 0)

        assert tracker.mark_done(tp, 0) == 1
