"""The lane map over a connector's life: creation, deletion, moves settling,
the per-minute upkeep script, and lane-count changes.

Runs the real Lua scripts on fakeredis, standalone and with the cluster double.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

from app.services.messaging.lanes import assignment as assignment_module
from app.services.messaging.lanes import lifecycle
from app.services.messaging.lanes.assignment import (
    LaneAssignments,
    LaneEntry,
    lane_meta_key,
    read_lane_map,
    write_lane_count,
)
from app.services.messaging.lanes.assignment_policy import (
    ConnectorClass,
    LaneRequestReason,
)
from app.services.messaging.lanes.hash_router import stable_lane
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.unit.services.messaging.test_lane_assignment import _colliding

TOPIC = "record-events"
LANES = 8


@pytest.fixture(params=[False, True], ids=["standalone", "cluster"])
def provider(request: pytest.FixtureRequest) -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=request.param)


def _assignments(provider: FakeRedisConnectionProvider, **overrides: object) -> LaneAssignments:
    options: dict[str, object] = {"topic": TOPIC, "fallback_lane_count": LANES}
    options.update(overrides)
    return LaneAssignments(logging.getLogger("test_lane_lifecycle"), provider, **options)  # type: ignore[arg-type]


async def _map(provider: FakeRedisConnectionProvider) -> dict[str, LaneEntry]:
    return await read_lane_map(provider.get_client(), TOPIC)


async def _meta(provider: FakeRedisConnectionProvider) -> dict[str, str]:
    return await _assignments(provider).read_meta()


class TestCreation:
    async def test_a_new_connector_takes_a_free_lane_and_owes_nothing_to_its_hash_lane(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        assignments = _assignments(provider)
        await assignments.assign(first, ConnectorClass.TEAM, is_new=True)

        lane = await assignments.assign(second, ConnectorClass.TEAM, is_new=True)

        entry = (await _map(provider))[second]
        assert lane != stable_lane(first, LANES)
        assert entry.prev_lane is None

    async def test_a_connector_that_already_has_a_lane_keeps_it_with_its_real_class(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        lane = await assignments.lane_for("gitlab-1")

        assert await assignments.assign("gitlab-1", ConnectorClass.PERSONAL) == lane
        assert (await _map(provider))["gitlab-1"].connector_class == "personal"

    async def test_the_class_known_at_creation_is_recorded(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)

        await assignments.assign("kb-1", ConnectorClass.KB, is_new=True)
        await assignments.assign("gmail-1", ConnectorClass.PERSONAL, is_new=True)

        entries = await _map(provider)
        assert (entries["kb-1"].connector_class, entries["gmail-1"].connector_class) == ("kb", "personal")


class TestDeletion:
    async def test_a_deleted_connectors_lane_goes_to_the_next_connector(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        for i in range(LANES):
            await assignments.assign(f"team-{i}", ConnectorClass.TEAM, is_new=True)
        freed = (await _map(provider))["team-3"].lane

        assert await assignments.release("team-3") is True
        lane = await assignments.assign("team-new", ConnectorClass.TEAM, is_new=True)

        assert lane == freed
        assert (await _meta(provider))[f"large:{freed}"] == "1"

    async def test_late_events_of_a_deleted_connector_still_go_to_its_lane(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider, cache_seconds=0)
        lane = await assignments.lane_for("gitlab-1")

        await assignments.release("gitlab-1")

        assert await assignments.lane_for("gitlab-1") == lane
        assert (await _map(provider))["gitlab-1"].state == "deleted"

    async def test_releasing_twice_or_a_connector_with_no_lane_changes_nothing(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for("gitlab-1")
        await assignments.release("gitlab-1")
        meta = await _meta(provider)

        assert await assignments.release("gitlab-1") is False
        assert await assignments.release("never-seen") is False
        assert await _meta(provider) == meta

    async def test_the_shared_default_entry_is_never_released(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for(DEFAULT_LANE_KEY)

        assert await assignments.release(DEFAULT_LANE_KEY) is False
        assert (await _map(provider))[DEFAULT_LANE_KEY].is_live


class TestUpkeep:
    async def _moved(self, provider: FakeRedisConnectionProvider) -> tuple[LaneAssignments, str, LaneEntry]:
        first, second = _colliding(2)
        assignments = _assignments(provider)
        await assignments.lane_for(first)
        await assignments.lane_for(second)
        return assignments, second, (await _map(provider))[second]

    async def test_a_move_is_fenced_only_after_producers_caches_have_run_out(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments, moved, _entry = await self._moved(provider)

        early = await assignments.upkeep({}, fence_delay_ms=90_000)
        assert early.fenced == 0
        assert (await _map(provider))[moved].fence_ms is None

        due = await assignments.upkeep({}, fence_delay_ms=0)
        assert due.fenced == 1
        assert (await _map(provider))[moved].fence_ms == due.now_ms

    async def test_a_move_settles_once_its_old_lane_has_finished_up_to_the_fence(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments, moved, entry = await self._moved(provider)
        fence = (await assignments.upkeep({}, fence_delay_ms=0)).now_ms
        old = entry.prev_lane
        assert old is not None

        still_busy = await assignments.upkeep({old: fence - 1}, fence_delay_ms=0)
        assert still_busy.cleared == 0
        assert (await _map(provider))[moved].prev_lane == old

        drained = await assignments.upkeep({old: fence + 1}, fence_delay_ms=0)
        assert drained.cleared == 1
        settled = (await _map(provider))[moved]
        assert (settled.prev_lane, settled.moved_at_ms, settled.fence_ms) == (None, None, None)

    async def test_nothing_that_needs_the_backlog_is_decided_without_it(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments, moved, entry = await self._moved(provider)
        await assignments.upkeep({}, fence_delay_ms=0)
        await assignments.release(_colliding(2)[0])
        meta = await _meta(provider)

        result = await assignments.upkeep(None, fence_delay_ms=0)

        assert result.cleared == 0
        assert (await _map(provider))[moved].prev_lane == entry.prev_lane
        assert (await _meta(provider)).get("busyAt") == meta.get("busyAt")

    async def test_a_deleted_entry_stays_so_a_late_event_keeps_its_lane_after_the_drain(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """A cleanup retried or re-sent by a sweep can come long after the
        delete. Were the entry removed once its lane drained, that event
        would be given a new live lane, counting as a large connector until
        the next pass released it, and steering a new connector meanwhile."""
        assignments = _assignments(provider, cache_seconds=0)
        lane = await assignments.lane_for("gitlab-1")
        await assignments.release("gitlab-1")
        fence = (await assignments.upkeep({}, fence_delay_ms=0)).now_ms

        drained = await assignments.upkeep({lane: fence + 10}, fence_delay_ms=0)

        assert drained.cleared == 0
        entry = (await _map(provider))["gitlab-1"]
        assert (entry.state, entry.lane, entry.fence_ms) == ("deleted", lane, fence)
        assert await assignments.lane_for("gitlab-1") == lane
        assert (await _map(provider))["gitlab-1"].state == "deleted"
        assert (await _meta(provider))[f"large:{lane}"] == "0"

    async def test_a_deleted_entry_lets_its_old_lane_go_once_that_has_drained(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments, moved, entry = await self._moved(provider)
        old = entry.prev_lane
        assert old is not None
        await assignments.release(moved)
        fence = (await assignments.upkeep({}, fence_delay_ms=0)).now_ms

        held = await assignments.upkeep({old: fence - 10}, fence_delay_ms=0)
        assert held.cleared == 0
        assert (await _map(provider))[moved].prev_lane == old

        drained = await assignments.upkeep({old: fence + 10}, fence_delay_ms=0)
        assert drained.cleared == 1
        settled = (await _map(provider))[moved]
        assert (settled.state, settled.lane, settled.prev_lane) == ("deleted", entry.lane, None)

    async def test_a_lane_whose_oldest_work_is_old_is_busy_until_the_reading_goes_stale(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        now = (await assignments.upkeep({}, fence_delay_ms=0)).now_ms

        await assignments.upkeep({2: now - 10 * 60 * 1000, 3: now}, fence_delay_ms=0)

        meta = await _meta(provider)
        assert (meta["busy:2"], meta["busy:3"], meta["busy:4"]) == ("1", "0", "0")
        hashed_to_busy = next(c for c in (f"c-{i}" for i in range(100)) if stable_lane(c, LANES) == 2)
        assert await _assignments(provider).lane_for(hashed_to_busy) != 2

    async def test_counts_that_drifted_are_rebuilt_from_the_map(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        lanes = [await assignments.lane_for(f"c-{i}") for i in range(5)]
        client = provider.get_client()
        await client.hset(lane_meta_key(TOPIC), mapping={f"large:{lanes[0]}": "40", "small:6": "9"})

        await assignments.upkeep({}, fence_delay_ms=0)

        meta = await _meta(provider)
        assert int(meta[f"large:{lanes[0]}"]) == lanes.count(lanes[0])
        assert meta["small:6"] == "0"

    async def test_the_version_only_moves_when_something_changed(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for("c-1")
        await assignments.upkeep({}, fence_delay_ms=0)
        version = (await _meta(provider))["version"]

        await assignments.upkeep({}, fence_delay_ms=0)

        assert (await _meta(provider))["version"] == version


class TestClassCorrection:
    async def test_a_guessed_class_is_corrected_and_the_counts_follow(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        lane = await assignments.lane_for("gmail-1")
        entry = (await _map(provider))["gmail-1"]

        assert await assignments.correct_class("gmail-1", entry, ConnectorClass.PERSONAL.value)

        meta = await _meta(provider)
        assert (await _map(provider))["gmail-1"] == LaneEntry(lane, "personal")
        assert (meta[f"large:{lane}"], meta[f"small:{lane}"]) == ("0", "1")

    async def test_an_entry_that_changed_since_it_was_read_is_left_alone(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for("gmail-1")
        stale = (await _map(provider))["gmail-1"]
        await assignments.release("gmail-1")

        assert not await assignments.correct_class("gmail-1", stale, ConnectorClass.PERSONAL.value)
        assert (await _map(provider))["gmail-1"].connector_class == "team"


class TestLaneCountChanges:
    async def test_lowering_then_raising_the_lane_count(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """Eight lanes down to four: entries on lanes four to seven are given a
        new lane on their next lookup and keep the old one to settle. Up to
        sixteen: nobody moves, and the new lanes go to new connectors."""
        client = provider.get_client()
        await write_lane_count(client, TOPIC, 8)
        assignments = _assignments(provider, cache_seconds=0)
        connectors = [f"team-{i}" for i in range(8)]
        before = {
            c: await assignments.assign(c, ConnectorClass.TEAM, is_new=True) for c in connectors
        }

        await write_lane_count(client, TOPIC, 4)
        after_lowering = {c: await assignments.lane_for(c) for c in connectors}

        entries = await _map(provider)
        for connector, lane in before.items():
            if lane < 4:
                assert after_lowering[connector] == lane
                assert entries[connector].prev_lane is None
            else:
                assert after_lowering[connector] < 4
                assert entries[connector].prev_lane == lane

        await write_lane_count(client, TOPIC, 16)
        after_raising = {c: await assignments.lane_for(c) for c in connectors}
        assert after_raising == after_lowering
        newcomer = await assignments.assign("team-new", ConnectorClass.TEAM, is_new=True)
        assert newcomer >= 4


class TestLifecycleHelpers:
    @pytest.fixture
    def in_use(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        assignments = MagicMock()
        assignments.assign = AsyncMock(return_value=3)
        assignments.release = AsyncMock(return_value=True)
        monkeypatch.setattr(lifecycle, "lane_assignments_in_use", lambda _topic: assignments)
        return assignments

    @pytest.mark.parametrize(
        ("connector_type", "scope", "expected"),
        [("KB", "personal", "kb"), ("GMAIL", "personal", "personal"), ("SLACK", "team", "team"), (None, None, "team")],
    )
    def test_the_class_comes_from_the_apps_document(
        self, connector_type: str | None, scope: str | None, expected: str
    ) -> None:
        assert lifecycle.connector_class_of(connector_type, scope) == expected

    async def test_a_new_connector_is_placed_as_new_with_its_class(self, in_use: MagicMock) -> None:
        await lifecycle.assign_lane_to_new_connector(
            logging.getLogger("t"), "slack-1", connector_type="SLACK", scope="team", org_id="org-1"
        )

        in_use.assign.assert_awaited_once_with(
            "slack-1", "team", org_id="org-1", connector_type="SLACK", is_new=True
        )

    async def test_a_failure_is_logged_and_never_raised(
        self, in_use: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        in_use.assign.side_effect = ConnectionError("Redis is down")
        in_use.release.side_effect = ConnectionError("Redis is down")

        with caplog.at_level(logging.WARNING):
            await lifecycle.assign_lane_to_new_connector(
                logging.getLogger("t"), "slack-1", connector_type="SLACK", scope="team", org_id="o"
            )
            await lifecycle.free_lane_of_deleted_connector(logging.getLogger("t"), "slack-1")

        assert sum("queue lane" in r.getMessage() for r in caplog.records) == 2

    async def test_with_hashing_there_is_nothing_to_do(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(assignment_module, "_shared", {})

        await lifecycle.assign_lane_to_new_connector(
            logging.getLogger("t"), "slack-1", connector_type="SLACK", scope="team", org_id="o"
        )
        await lifecycle.free_lane_of_deleted_connector(logging.getLogger("t"), "slack-1")

    def test_the_process_map_is_the_one_its_producers_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = FakeRedisConnectionProvider()
        monkeypatch.setattr(assignment_module, "_shared", {})
        shared = assignment_module.shared_lane_assignments(
            logging.getLogger("t"), provider, topic=TOPIC, fallback_lane_count=LANES, cache_seconds=60
        )

        assert assignment_module.lane_assignments_in_use(TOPIC) is shared
        assert assignment_module.lane_assignments_in_use("other-topic") is None


async def test_a_connector_can_move_again_once_its_last_move_has_settled(
    provider: FakeRedisConnectionProvider,
) -> None:
    first, second = _colliding(2)
    assignments = _assignments(provider)
    await assignments.lane_for(first)
    await assignments.lane_for(second)
    old = (await _map(provider))[second].prev_lane
    assert old is not None
    fence = (await assignments.upkeep({}, fence_delay_ms=0)).now_ms
    await assignments.upkeep({old: fence + 1}, fence_delay_ms=0)
    current = (await _map(provider))[second].lane
    target = (current + 1) % LANES
    mover = _assignments(provider, choose=lambda _r, _s: target)

    moved = await mover.move(second, LaneRequestReason.UPGRADE)

    assert (moved.lane, moved.prev_lane) == (target, current)
