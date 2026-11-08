"""Tests for ListingCheckpoint: where an incremental listing stands.

One place for what used to be a get/update helper pair per listing on the connector.
"""

import pytest

from app.connectors.sources.linear.listing_checkpoint import (
    OVERLAP_MS,
    ListingCheckpoint,
    ListingRun,
)

NOW = 1_700_000_000_000
EMPTY = {"last_sync_time": 0, "resume_cursor": "", "resume_since": 0, "resume_started_at": 0, "held_runs": 0}


class FakeSyncPoint:
    """The two SyncPoint methods the checkpoint uses, over a dict."""

    def __init__(self, initial=None) -> None:
        self.store = dict(initial or {})
        self.writes = []

    async def read_sync_point(self, key) -> dict | None:
        value = self.store.get(key)
        return dict(value) if value is not None else None

    async def update_sync_point(self, key, data) -> None:
        self.writes.append((key, dict(data)))
        self.store[key] = dict(data)


class Clock:
    def __init__(self, now=NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _make(initial=None, key="team_issues_team-1", now=NOW) -> tuple:
    sync_point = FakeSyncPoint({key: initial} if initial is not None else None)
    clock = Clock(now)
    return ListingCheckpoint(sync_point, key, clock), sync_point, clock


class TestListingRun:
    def test_full_when_there_is_no_since(self) -> None:
        assert ListingRun(None, NOW).is_full is True
        assert ListingRun(NOW - 1, NOW).is_full is False

    def test_defaults(self) -> None:
        run = ListingRun(None, NOW)
        assert run.cursor is None
        assert run.resumed is False


class TestBegin:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", [None, {}, dict(EMPTY)])
    async def test_first_run_reads_everything(self, stored) -> None:
        checkpoint, sync_point, _ = _make(stored)

        run = await checkpoint.begin()

        assert run == ListingRun(since=None, started_at=NOW, cursor=None, resumed=False)
        assert run.is_full
        # Beginning writes nothing: a run that dies at once leaves no trace.
        assert sync_point.writes == []

    @pytest.mark.asyncio
    async def test_later_run_rereads_the_overlap_before_the_last_start(self) -> None:
        last = NOW - 3_600_000
        checkpoint, _, _ = _make({"last_sync_time": last})

        run = await checkpoint.begin()

        assert OVERLAP_MS == 60_000
        assert run.since == last - OVERLAP_MS
        assert run.started_at == NOW
        assert run.is_full is False
        assert run.cursor is None and run.resumed is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("last", [1, 30_000, OVERLAP_MS])
    async def test_overlap_never_turns_an_incremental_run_into_a_full_one(self, last) -> None:
        checkpoint, _, _ = _make({"last_sync_time": last})

        run = await checkpoint.begin()

        assert run.since == 1
        assert run.is_full is False

    @pytest.mark.asyncio
    async def test_interrupted_run_is_resumed_as_it_was(self) -> None:
        checkpoint, _, clock = _make({
            "last_sync_time": NOW - 7_200_000,
            "resume_cursor": "cursor-9",
            "resume_since": NOW - 7_260_000,
            "resume_started_at": NOW - 600_000,
        })
        clock.now = NOW + 5_000

        run = await checkpoint.begin()

        # Its own since and its own start time, not ones worked out again from now.
        assert run == ListingRun(
            since=NOW - 7_260_000, started_at=NOW - 600_000, cursor="cursor-9", resumed=True)

    @pytest.mark.asyncio
    async def test_interrupted_full_run_resumes_as_a_full_run(self) -> None:
        checkpoint, _, _ = _make({
            "last_sync_time": 0, "resume_cursor": "cursor-3",
            "resume_since": 0, "resume_started_at": NOW - 600_000,
        })

        run = await checkpoint.begin()

        assert run.is_full is True
        assert run.cursor == "cursor-3"
        assert run.resumed is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("leftover", [
        {"resume_cursor": "cursor-9", "resume_started_at": 0},
        {"resume_cursor": "", "resume_started_at": NOW - 600_000},
    ])
    async def test_half_a_resume_record_is_not_a_resume(self, leftover) -> None:
        checkpoint, _, _ = _make({"last_sync_time": NOW - 3_600_000, **leftover})

        run = await checkpoint.begin()

        assert run.resumed is False
        assert run.cursor is None
        assert run.since == NOW - 3_600_000 - OVERLAP_MS
        assert run.started_at == NOW


class TestAdvance:
    @pytest.mark.asyncio
    async def test_saves_the_cursor_with_the_runs_since_and_start(self) -> None:
        last = NOW - 3_600_000
        checkpoint, sync_point, _ = _make({"last_sync_time": last, "held_runs": 2})
        run = await checkpoint.begin()

        await checkpoint.advance(run, "cursor-1")

        assert run.cursor == "cursor-1"
        assert sync_point.store["team_issues_team-1"] == {
            "last_sync_time": last,  # the checkpoint itself has not moved
            "resume_cursor": "cursor-1",
            "resume_since": last - OVERLAP_MS,
            "resume_started_at": NOW,
            "held_runs": 2,
        }

    @pytest.mark.asyncio
    async def test_full_run_saves_zero_for_since(self) -> None:
        checkpoint, sync_point, _ = _make()
        run = await checkpoint.begin()

        await checkpoint.advance(run, "cursor-1")

        assert sync_point.store["team_issues_team-1"] == {
            **EMPTY, "resume_cursor": "cursor-1", "resume_started_at": NOW}

    @pytest.mark.asyncio
    async def test_each_page_replaces_the_saved_cursor(self) -> None:
        checkpoint, sync_point, _ = _make()
        run = await checkpoint.begin()

        await checkpoint.advance(run, "cursor-1")
        await checkpoint.advance(run, "cursor-2")

        assert sync_point.store["team_issues_team-1"]["resume_cursor"] == "cursor-2"
        assert run.cursor == "cursor-2"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("cursor", [None, ""])
    async def test_page_without_a_cursor_saves_nothing(self, cursor) -> None:
        checkpoint, sync_point, _ = _make()
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")
        writes = len(sync_point.writes)

        await checkpoint.advance(run, cursor)

        assert len(sync_point.writes) == writes
        assert run.cursor == "cursor-1"

    @pytest.mark.asyncio
    async def test_next_begin_continues_from_the_saved_cursor(self) -> None:
        checkpoint, _, clock = _make({"last_sync_time": NOW - 3_600_000})
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-4")

        clock.now = NOW + 900_000  # the process died; the next sync starts later
        resumed = await checkpoint.begin()

        assert resumed == ListingRun(
            since=run.since, started_at=NOW, cursor="cursor-4", resumed=True)


class TestFinish:
    @pytest.mark.asyncio
    async def test_moves_to_when_the_run_started_not_when_it_ended(self) -> None:
        checkpoint, sync_point, clock = _make({"last_sync_time": NOW - 3_600_000, "held_runs": 2})
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")

        clock.now = NOW + 300_000  # reading the list took five minutes
        await checkpoint.finish(run)

        # A change made during those five minutes is after the checkpoint.
        assert sync_point.store["team_issues_team-1"] == {**EMPTY, "last_sync_time": NOW}

    @pytest.mark.asyncio
    async def test_finishing_a_resumed_run_uses_the_original_start(self) -> None:
        started = NOW - 600_000
        checkpoint, sync_point, _ = _make({
            "last_sync_time": NOW - 7_200_000, "resume_cursor": "cursor-9",
            "resume_since": NOW - 7_260_000, "resume_started_at": started,
        })
        run = await checkpoint.begin()

        await checkpoint.finish(run)

        assert sync_point.store["team_issues_team-1"] == {**EMPTY, "last_sync_time": started}
        assert await checkpoint.last_completed() == started

    @pytest.mark.asyncio
    async def test_run_after_a_finished_one_is_incremental(self) -> None:
        checkpoint, _, clock = _make()
        first = await checkpoint.begin()
        await checkpoint.finish(first)

        clock.now = NOW + 3_600_000
        second = await checkpoint.begin()

        assert first.is_full is True
        assert second == ListingRun(since=NOW - OVERLAP_MS, started_at=NOW + 3_600_000)

    @pytest.mark.asyncio
    async def test_unfinished_run_does_not_move_the_checkpoint(self) -> None:
        last = NOW - 3_600_000
        checkpoint, _, _ = _make({"last_sync_time": last})
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")

        assert await checkpoint.last_completed() == last


class TestHold:
    @pytest.mark.asyncio
    async def test_keeps_the_checkpoint_and_counts_the_run(self) -> None:
        last = NOW - 3_600_000
        checkpoint, sync_point, _ = _make({"last_sync_time": last})
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")

        assert await checkpoint.hold() == 1

        # The list was read to its end, so there is nothing to resume: the next run
        # reads the same window again from the top.
        assert sync_point.store["team_issues_team-1"] == {**EMPTY, "last_sync_time": last, "held_runs": 1}
        again = await checkpoint.begin()
        assert again.since == last - OVERLAP_MS
        assert again.resumed is False

    @pytest.mark.asyncio
    async def test_counts_consecutive_holds(self) -> None:
        checkpoint, _, _ = _make({"last_sync_time": NOW - 3_600_000})

        assert [await checkpoint.hold() for _ in range(3)] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_hold_on_a_first_run_keeps_it_a_full_run(self) -> None:
        checkpoint, _, _ = _make()

        assert await checkpoint.hold() == 1

        assert (await checkpoint.begin()).is_full is True

    @pytest.mark.asyncio
    async def test_finish_ends_the_streak(self) -> None:
        checkpoint, sync_point, _ = _make({"last_sync_time": NOW - 3_600_000})
        await checkpoint.hold()
        await checkpoint.hold()

        await checkpoint.finish(await checkpoint.begin())

        assert sync_point.store["team_issues_team-1"]["held_runs"] == 0
        assert await checkpoint.hold() == 1


class TestAbandonCursor:
    @pytest.mark.asyncio
    async def test_run_restarts_from_the_top_with_the_same_window(self) -> None:
        last = NOW - 7_200_000
        checkpoint, sync_point, _ = _make({
            "last_sync_time": last, "resume_cursor": "stale",
            "resume_since": last - OVERLAP_MS, "resume_started_at": NOW - 600_000, "held_runs": 1,
        })
        run = await checkpoint.begin()

        await checkpoint.abandon_cursor(run)

        assert run.cursor is None
        assert run.resumed is False
        # What the run covers has not changed.
        assert run.since == last - OVERLAP_MS
        assert run.started_at == NOW - 600_000
        assert sync_point.store["team_issues_team-1"] == {**EMPTY, "last_sync_time": last, "held_runs": 1}

    @pytest.mark.asyncio
    async def test_pages_read_after_abandoning_are_saved_again(self) -> None:
        checkpoint, sync_point, _ = _make({
            "last_sync_time": 0, "resume_cursor": "stale",
            "resume_since": 0, "resume_started_at": NOW - 600_000,
        })
        run = await checkpoint.begin()
        await checkpoint.abandon_cursor(run)

        await checkpoint.advance(run, "fresh-1")

        saved = sync_point.store["team_issues_team-1"]
        assert saved["resume_cursor"] == "fresh-1"
        assert saved["resume_started_at"] == NOW - 600_000


class TestReset:
    @pytest.mark.asyncio
    async def test_forgets_everything(self) -> None:
        checkpoint, sync_point, _ = _make({
            "last_sync_time": NOW - 3_600_000, "resume_cursor": "cursor-1",
            "resume_since": 5, "resume_started_at": NOW - 10, "held_runs": 2,
        })

        await checkpoint.reset()

        assert sync_point.store["team_issues_team-1"] == EMPTY
        assert await checkpoint.last_completed() is None
        assert (await checkpoint.begin()).is_full is True


class TestStorage:
    @pytest.mark.asyncio
    async def test_last_completed(self) -> None:
        assert await _make()[0].last_completed() is None
        assert await _make({"last_sync_time": 0})[0].last_completed() is None
        assert await _make({"last_sync_time": NOW})[0].last_completed() == NOW

    @pytest.mark.asyncio
    async def test_values_are_flat_and_never_none(self) -> None:
        checkpoint, sync_point, _ = _make()
        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")
        await checkpoint.hold()
        await checkpoint.finish(run)
        await checkpoint.reset()

        for _, written in sync_point.writes:
            assert set(written) == set(EMPTY)
            assert all(isinstance(value, (int, str)) for value in written.values())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", [
        "team_issues_team-1", "attachments", "documents", "projects", "trashed_issues", "comment_activity",
    ])
    async def test_stored_under_its_own_key(self, key) -> None:
        checkpoint, sync_point, _ = _make(key=key)

        await checkpoint.finish(await checkpoint.begin())

        assert list(sync_point.store) == [key]

    @pytest.mark.asyncio
    async def test_checkpoints_sharing_a_sync_point_do_not_touch_each_other(self) -> None:
        sync_point = FakeSyncPoint()
        clock = Clock()
        team_a = ListingCheckpoint(sync_point, "team_issues_team-a", clock)
        team_b = ListingCheckpoint(sync_point, "team_issues_team-b", clock)

        run_a = await team_a.begin()
        await team_a.advance(run_a, "a-cursor")
        await team_b.finish(await team_b.begin())
        await team_a.reset()

        assert sync_point.store["team_issues_team-b"] == {**EMPTY, "last_sync_time": NOW}
        assert sync_point.store["team_issues_team-a"] == EMPTY

    @pytest.mark.asyncio
    async def test_unknown_stored_fields_are_dropped_on_write(self) -> None:
        # What the per-key helpers used to store: only last_sync_time carries over.
        checkpoint, sync_point, _ = _make({"last_sync_time": NOW - 10_000_000, "legacy": "x"})

        run = await checkpoint.begin()
        await checkpoint.advance(run, "cursor-1")

        assert run.since == NOW - 10_000_000 - OVERLAP_MS
        assert "legacy" not in sync_point.store["team_issues_team-1"]
