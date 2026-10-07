"""The lane map: its scripts, its cache, and the router producers use.

Runs the real Lua scripts against an in-memory Redis (fakeredis with Lua),
standalone and with the cluster double that refuses a script whose keys span
hash slots. ``tests/integration/messaging/test_redis_lane_assignment_it.py`` runs
the same placements against a real Redis 7 server.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from itertools import count

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.lanes import assignment as assignment_module
from app.services.messaging.lanes.assignment import (
    AssignedRedisLaneRouter,
    LaneAssignments,
    LaneEntry,
    LaneMoveRefusedError,
    lane_map_key,
    lane_meta_key,
    read_lane_map,
    write_lane_count,
)
from app.services.messaging.lanes.assignment_policy import (
    KEEP_CURRENT,
    ConnectorClass,
    LaneRequestReason,
    choose_connector_lane,
)
from app.services.messaging.lanes.hash_router import (
    KafkaLaneRouter,
    RedisLaneRouter,
    stable_lane,
)
from app.services.messaging.lanes.interface import (
    DEFAULT_LANE_KEY,
    LaneAssignmentMode,
    LaneConfig,
    LaneHint,
)
from app.services.messaging.lanes.producer import LaneAwareProducer
from app.services.messaging.messaging_factory import MessagingFactory
from app.telemetry.backend import METRICS_BACKEND
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.unit.services.messaging.test_lane_aware_producer import _RecordingProducer

TOPIC = "record-events"
LANES = 8


def _colliding(how_many: int, lane_count: int = LANES, prefix: str = "connector") -> list[str]:
    """Connector ids that all hash to one lane, as two of the reporter's did."""
    by_lane: dict[int, list[str]] = {}
    for i in count():
        name = f"{prefix}-{i}"
        same = by_lane.setdefault(stable_lane(name, lane_count), [])
        same.append(name)
        if len(same) == how_many:
            return same
    raise AssertionError("unreachable")


@pytest.fixture(params=[False, True], ids=["standalone", "cluster"])
def provider(request: pytest.FixtureRequest) -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=request.param)


def _assignments(provider: FakeRedisConnectionProvider, **overrides: object) -> LaneAssignments:
    options: dict[str, object] = {"topic": TOPIC, "fallback_lane_count": LANES}
    options.update(overrides)
    return LaneAssignments(logging.getLogger("test_lane_assignment"), provider, **options)  # type: ignore[arg-type]


async def _map(provider: FakeRedisConnectionProvider) -> dict[str, LaneEntry]:
    return await read_lane_map(provider.get_client(), TOPIC)


async def _meta(provider: FakeRedisConnectionProvider) -> dict[str, str]:
    raw = await provider.get_client().hgetall(lane_meta_key(TOPIC))
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


class TestEntries:
    def test_an_entry_round_trips(self) -> None:
        entry = LaneEntry(5, "team", prev_lane=3, moved_at_ms=1000, fence_ms=2000)

        assert LaneEntry.parse(entry.encode()) == entry
        assert entry.encode() == "v1|5|team|live|3|1000|2000"

    @pytest.mark.parametrize(
        "raw", ["", "v2|5|team|live|||", "v1|five|team|live|||", "v1|5|team", None, 5]
    )
    def test_anything_malformed_is_no_entry(self, raw: object) -> None:
        assert LaneEntry.parse(raw) is None

    def test_both_keys_share_one_cluster_slot(self) -> None:
        provider = FakeRedisConnectionProvider(is_cluster=True)

        assert provider.key_slot(lane_map_key(TOPIC)) == provider.key_slot(lane_meta_key(TOPIC))


class TestPlacement:
    async def test_two_connectors_that_hash_to_one_lane_get_different_lanes(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        gitlab, slack = _colliding(2)
        assignments = _assignments(provider)

        assert await assignments.lane_for(gitlab) != await assignments.lane_for(slack)

    async def test_a_connector_whose_hash_lane_is_free_keeps_it_with_nothing_to_settle(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)

        lane = await assignments.lane_for("gitlab-1")

        assert lane == stable_lane("gitlab-1", LANES)
        assert (await _map(provider))["gitlab-1"] == LaneEntry(lane, "team")

    async def test_a_connector_moved_off_its_hash_lane_keeps_it_as_the_previous_lane(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """It may have events there from before the map existed."""
        first, second = _colliding(2)
        assignments = _assignments(provider)
        await assignments.lane_for(first)

        await assignments.lane_for(second)

        entry = (await _map(provider))[second]
        assert entry.prev_lane == stable_lane(second, LANES)
        assert entry.moved_at_ms is not None
        assert entry.fence_ms is None

    async def test_a_connector_created_after_the_map_has_no_previous_lane(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        assignments = _assignments(provider)
        await assignments.lane_for(first)

        await assignments.assign(second, ConnectorClass.TEAM, is_new=True)

        assert (await _map(provider))[second].prev_lane is None

    async def test_a_second_lookup_returns_the_recorded_lane_without_placing_again(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        connectors = _colliding(3)
        assignments = _assignments(provider, cache_seconds=0)
        first = [await assignments.lane_for(c) for c in connectors]
        version = (await _meta(provider))["version"]

        again = [await assignments.lane_for(c) for c in connectors]

        assert again == first
        assert (await _meta(provider))["version"] == version

    async def test_eight_team_connectors_on_one_hash_lane_end_up_on_eight_lanes(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)

        lanes = [await assignments.lane_for(c) for c in _colliding(8)]

        assert sorted(lanes) == list(range(LANES))

    async def test_the_counts_follow_the_map(self, provider: FakeRedisConnectionProvider) -> None:
        assignments = _assignments(provider)
        for i in range(12):
            await assignments.lane_for(f"team-{i}")
        for i in range(10):
            await assignments.lane_for(f"kb-{i}", LaneHint(connector_class=ConnectorClass.KB.value))

        entries = await _map(provider)
        meta = await _meta(provider)
        large = Counter(e.lane for e in entries.values() if e.connector_class == "team")
        small = Counter(e.lane for e in entries.values() if e.connector_class != "team")
        for lane in range(LANES):
            assert int(meta.get(f"large:{lane}", 0)) == large[lane]
            assert int(meta.get(f"small:{lane}", 0)) == small[lane]

    async def test_knowledge_bases_stay_off_lanes_with_a_team_connector(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        team_lanes = {await assignments.lane_for(f"team-{i}") for i in range(3)}

        kb_lanes = {
            await assignments.lane_for(f"kb-{i}", LaneHint(connector_class=ConnectorClass.KB.value))
            for i in range(20)
        }

        assert len(team_lanes) == 3
        assert not team_lanes & kb_lanes

    async def test_events_without_a_connector_share_one_small_system_entry(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)

        await assignments.lane_for(DEFAULT_LANE_KEY)

        assert (await _map(provider))[DEFAULT_LANE_KEY].connector_class == "system"

    async def test_the_lane_count_the_consumer_recorded_wins_over_the_producers_own(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        await write_lane_count(provider.get_client(), TOPIC, 4)
        assignments = _assignments(provider, fallback_lane_count=16)

        lanes = {await assignments.lane_for(f"c-{i}") for i in range(10)}

        assert lanes <= set(range(4))
        assert assignments.lane_count == 4

    async def test_recording_the_same_lane_count_again_changes_nothing(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        client = provider.get_client()

        assert await write_lane_count(client, TOPIC, 8) is True
        assert await write_lane_count(client, TOPIC, 8) is False
        assert (await _meta(provider))["version"] == "1"


class TestEntriesThatNeedReplacing:
    async def test_a_lane_outside_a_lowered_lane_count_is_reassigned_keeping_the_old_one(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        client = provider.get_client()
        await client.hset(lane_map_key(TOPIC), "gitlab-1", LaneEntry(6, "team").encode())
        await client.hset(lane_meta_key(TOPIC), mapping={"laneCount": "4", "large:6": "1"})
        assignments = _assignments(provider)

        lane = await assignments.lane_for("gitlab-1")

        entry = (await _map(provider))["gitlab-1"]
        assert lane < 4
        assert entry.lane == lane
        assert entry.prev_lane == 6
        assert (await _meta(provider))["large:6"] == "0"

    @pytest.mark.parametrize("connector_class", ["personal", "kb"])
    async def test_a_replaced_entry_keeps_the_class_it_had(
        self, provider: FakeRedisConnectionProvider, connector_class: str
    ) -> None:
        """An event says its class only for an upload; a small connector's
        entry must not turn large because some other event repaired it."""
        client = provider.get_client()
        await client.hset(lane_map_key(TOPIC), "small-1", LaneEntry(6, connector_class).encode())
        await client.hset(lane_meta_key(TOPIC), mapping={"laneCount": "4", "small:6": "1"})

        lane = await _assignments(provider).lane_for("small-1")

        entry = (await _map(provider))["small-1"]
        assert (entry.lane, entry.connector_class, entry.prev_lane) == (lane, connector_class, 6)
        meta = await _meta(provider)
        assert meta["small:6"] == "0"
        assert meta[f"small:{lane}"] == "1"
        assert not any(k.startswith("large:") and v != "0" for k, v in meta.items())

    async def test_an_entry_outside_the_lane_count_still_settling_a_move_is_left_alone(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """Replacing it would drop the lane its earlier events still wait on;
        it keeps publishing to its current lane, which the consumer adopted."""
        client = provider.get_client()
        settling = LaneEntry(6, "team", prev_lane=2, moved_at_ms=1000)
        await client.hset(lane_map_key(TOPIC), "gitlab-1", settling.encode())
        await client.hset(lane_meta_key(TOPIC), mapping={"laneCount": "4"})

        assert await _assignments(provider).lane_for("gitlab-1") == 6
        assert (await _map(provider))["gitlab-1"] == settling

    async def test_a_corrupt_entry_is_replaced_keeping_the_hash_lane_as_the_old_one(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        client = provider.get_client()
        assignments = _assignments(provider)
        await assignments.lane_for(first)
        await client.hset(lane_map_key(TOPIC), second, "garbage")

        await assignments.lane_for(second)

        entry = (await _map(provider))[second]
        assert entry.lane != stable_lane(second, LANES)
        assert entry.prev_lane == stable_lane(second, LANES)


class TestKnownClassAndLaneCount:
    async def test_creation_corrects_a_class_a_first_publish_guessed(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        lane = await assignments.lane_for("gmail-1")
        assert (await _map(provider))["gmail-1"].connector_class == "team"

        assert await assignments.assign("gmail-1", ConnectorClass.PERSONAL) == lane

        assert (await _map(provider))["gmail-1"] == LaneEntry(lane, "personal")
        meta = await _meta(provider)
        assert (meta[f"large:{lane}"], meta[f"small:{lane}"]) == ("0", "1")

    async def test_creation_racing_a_first_publish_still_records_the_real_class(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """Both lookups miss; the publish places first and caches its guess.
        Creation must still correct the class rather than take the cache.

        Gated so the order is certain: the publish looks up, then creation
        looks up, and only then does the publish commit."""
        assignments = _assignments(provider)
        real_eval = assignments._eval
        publish_looked_up = asyncio.Event()
        creation_looked_up = asyncio.Event()
        lookups = 0

        async def gated_eval(body: str, *args: object) -> list:
            nonlocal lookups
            if body == assignment_module._COMMIT_SCRIPT and not creation_looked_up.is_set():
                await creation_looked_up.wait()
            reply = await real_eval(body, *args)
            if body == assignment_module._LOOKUP_SCRIPT:
                lookups += 1
                (publish_looked_up if lookups == 1 else creation_looked_up).set()
            return reply

        assignments._eval = gated_eval  # type: ignore[method-assign]

        publish = asyncio.create_task(assignments.lane_for("gmail-1"))
        await publish_looked_up.wait()
        created = await assignments.assign("gmail-1", ConnectorClass.PERSONAL)
        published = await publish

        assert lookups == 2, "both lookups missed"
        assert published == created
        assert (await _map(provider))["gmail-1"].connector_class == "personal"
        meta = await _meta(provider)
        assert (meta[f"large:{created}"], meta[f"small:{created}"]) == ("0", "1")

    async def test_a_move_retried_after_a_class_correction_keeps_the_corrected_class(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        client = provider.get_client()
        shared = stable_lane(first, LANES)
        await client.hset(
            lane_map_key(TOPIC),
            mapping={first: LaneEntry(shared, "team").encode(), second: LaneEntry(shared, "team").encode()},
        )
        await client.hset(lane_meta_key(TOPIC), mapping={f"large:{shared}": "2", "laneCount": "8"})
        mover = _assignments(provider)
        creator = _assignments(provider)
        real_eval = mover._eval
        raced = False

        async def correction_lands_first(body: str, *args: object) -> list:
            nonlocal raced
            if body == assignment_module._COMMIT_SCRIPT and not raced:
                raced = True
                await creator.assign(second, ConnectorClass.PERSONAL)
            return await real_eval(body, *args)

        mover._eval = correction_lands_first  # type: ignore[method-assign]

        moved = await mover.move(second, LaneRequestReason.UPGRADE)

        entry = (await _map(provider))[second]
        assert (entry.lane, entry.connector_class) == (moved.lane, "personal")
        meta = await _meta(provider)
        assert meta[f"small:{moved.lane}"] == "1"
        assert meta.get(f"large:{moved.lane}", "0") == "0"
        assert await _assignments(provider).lane_for(second) == moved.lane

    async def test_a_class_correction_after_a_move_caches_the_lane_it_moved_to(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """Creation looked the row up; a move then committed elsewhere before
        the correction ran. The process must not cache the lane it left."""
        first, second = _colliding(2)
        shared = stable_lane(first, LANES)
        client = provider.get_client()
        await client.hset(
            lane_map_key(TOPIC),
            mapping={first: LaneEntry(shared, "team").encode(), second: LaneEntry(shared, "team").encode()},
        )
        await client.hset(lane_meta_key(TOPIC), mapping={f"large:{shared}": "2", "laneCount": "8"})
        creator = _assignments(provider)
        mover = _assignments(provider)
        real_eval = creator._eval
        moved_to: list[int] = []

        async def move_lands_between(body: str, *args: object) -> list:
            if body == assignment_module._COMMIT_SCRIPT and not moved_to:
                moved_to.append((await mover.move(second, LaneRequestReason.UPGRADE)).lane)
            return await real_eval(body, *args)

        creator._eval = move_lands_between  # type: ignore[method-assign]

        await creator.assign(second, ConnectorClass.PERSONAL)

        assert moved_to and moved_to[0] != shared
        assert await creator.lane_for(second) == moved_to[0]
        assert (await _map(provider))[second].connector_class == "personal"

    async def test_a_lowered_lane_count_seen_by_any_lookup_drops_cached_lanes_past_it(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        client = provider.get_client()
        await write_lane_count(client, TOPIC, 8)
        assignments = _assignments(provider)
        high = next(c for c in (f"c-{i}" for i in range(100)) if stable_lane(c, 8) >= 4)
        assert await assignments.lane_for(high) >= 4

        await write_lane_count(client, TOPIC, 4)
        await assignments.lane_for("another-connector")

        assert await assignments.lane_for(high) < 4


class TestConcurrentPlacement:
    async def test_a_commit_that_lost_a_race_asks_the_rule_again_on_what_won(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """The rule saw a free lane; by the time it committed another process
        had taken it. The commit is refused and the rule asked again."""
        first, second = _colliding(2)
        rival = _assignments(provider)
        calls: list[int] = []

        def counting_rule(request, snapshot):  # noqa: ANN202
            calls.append(snapshot.version)
            return choose_connector_lane(request, snapshot)

        assignments = _assignments(provider, choose=counting_rule)
        real_eval = assignments._eval
        raced = False

        async def eval_with_a_rival(body: str, *args: object) -> list:
            nonlocal raced
            if body == assignment_module._COMMIT_SCRIPT and not raced:
                raced = True
                await rival.lane_for(first)
            return await real_eval(body, *args)

        assignments._eval = eval_with_a_rival  # type: ignore[method-assign]

        lane = await assignments.lane_for(second)

        assert len(calls) == 2, "asked again after the refused commit"
        assert calls[1] > calls[0]
        assert lane != await rival.lane_for(first)

    async def test_many_lookups_from_two_processes_leave_exactly_one_entry_each(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """200 lookups for 100 connectors, interleaved across two processes'
        worth of caches and tasks."""
        processes = [_assignments(provider), _assignments(provider)]
        connectors = [f"c-{i}" for i in range(100)]

        async def look_up(index: int) -> tuple[str, int]:
            connector = connectors[index % len(connectors)]
            return connector, await processes[index % 2].lane_for(connector)

        answers = await asyncio.gather(*(look_up(i) for i in range(200)))

        entries = await _map(provider)
        assert set(entries) == set(connectors)
        for connector, lane in answers:
            assert entries[connector].lane == lane
        per_lane = Counter(entry.lane for entry in entries.values())
        assert max(per_lane.values()) - min(per_lane.values()) <= 1
        meta = await _meta(provider)
        assert sum(int(meta.get(f"large:{lane}", 0)) for lane in range(LANES)) == 100


class TestTheEditionHook:
    async def test_an_edition_rule_decides_the_lane(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        pinned = _assignments(provider, choose=lambda _request, _snapshot: 7)

        assert {await pinned.lane_for(f"c-{i}") for i in range(5)} == {7}

    async def test_the_rule_sees_who_is_asking_and_every_lanes_load(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        seen = []

        def recording_rule(request, snapshot):  # noqa: ANN202
            seen.append((request, snapshot))
            return choose_connector_lane(request, snapshot)

        await _assignments(provider).lane_for("team-1")
        assignments = _assignments(provider, choose=recording_rule)

        await assignments.lane_for(
            "slack-1", LaneHint(org_id="org-1", connector_type="SLACK")
        )

        request, snapshot = seen[0]
        assert (request.connector_id, request.org_id, request.connector_type) == (
            "slack-1",
            "org-1",
            "SLACK",
        )
        assert request.connector_class == "team"
        assert request.reason is LaneRequestReason.FIRST_ASSIGNMENT
        assert request.hash_lane == stable_lane("slack-1", LANES)
        assert snapshot.lane_count == LANES
        assert sum(load.large for load in snapshot.lanes) == 1
        assert snapshot.fields["version"] == "1"

    async def test_a_lane_outside_the_lane_count_is_refused(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        broken = _assignments(provider, choose=lambda _request, _snapshot: 99)

        with pytest.raises(ValueError, match="outside"):
            await broken.lane_for("c-1")
        assert await _map(provider) == {}

    async def test_keeping_a_lane_the_connector_does_not_have_is_refused(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        keeper = _assignments(provider, choose=lambda _request, _snapshot: KEEP_CURRENT)

        with pytest.raises(ValueError, match="no lane"):
            await keeper.lane_for("c-1")


class TestMoves:
    async def test_this_edition_refuses_an_administrator_move_and_writes_nothing(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for("slack-1")
        before = await _map(provider)

        with pytest.raises(LaneMoveRefusedError, match="not available"):
            await assignments.move("slack-1", LaneRequestReason.ADMIN, org_id="org-1")

        assert await _map(provider) == before

    async def test_an_edition_that_allows_it_can_move_a_connector(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        await _assignments(provider).lane_for(first)
        lanes = iter([stable_lane(first, LANES), 3])
        assignments = _assignments(
            provider,
            choose=lambda _request, _snapshot: next(lanes),
            admin_move_allowed=lambda _org, _connector: True,
        )
        await assignments.lane_for(second)

        moved = await assignments.move(second, LaneRequestReason.ADMIN)

        assert moved.lane == 3
        assert moved.prev_lane == stable_lane(first, LANES)

    async def test_an_upgrade_move_judges_the_lanes_without_the_connector_itself(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        """Two team connectors forced onto one lane: the move takes the other
        off it, and the counts follow."""
        first, second = _colliding(2)
        client = provider.get_client()
        shared = stable_lane(first, LANES)
        await client.hset(
            lane_map_key(TOPIC),
            mapping={first: LaneEntry(shared, "team").encode(), second: LaneEntry(shared, "team").encode()},
        )
        await client.hset(lane_meta_key(TOPIC), mapping={f"large:{shared}": "2", "laneCount": "8"})
        assignments = _assignments(provider)

        moved = await assignments.move(second, LaneRequestReason.UPGRADE)

        assert moved.lane != shared
        assert moved.prev_lane == shared
        meta = await _meta(provider)
        assert meta[f"large:{shared}"] == "1"
        assert meta[f"large:{moved.lane}"] == "1"
        assert await assignments.lane_for(second) == moved.lane

    async def test_a_second_move_while_the_first_is_settling_is_refused(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        first, second = _colliding(2)
        await _assignments(provider).lane_for(first)
        await _assignments(provider).lane_for(second)
        settling_lane = (await _map(provider))[second].lane
        elsewhere = _assignments(
            provider, choose=lambda _request, _snapshot: (settling_lane + 1) % LANES
        )

        with pytest.raises(LaneMoveRefusedError, match="settling"):
            await elsewhere.move(second, LaneRequestReason.UPGRADE)

    async def test_a_move_the_rule_answers_with_the_same_lane_changes_nothing(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        assignments = _assignments(provider)
        await assignments.lane_for("gitlab-1")
        before = await _map(provider)

        kept = await assignments.move("gitlab-1", LaneRequestReason.UPGRADE)

        assert kept == before["gitlab-1"]
        assert await _map(provider) == before

    async def test_a_connector_with_no_lane_cannot_be_moved(
        self, provider: FakeRedisConnectionProvider
    ) -> None:
        with pytest.raises(LaneMoveRefusedError, match="no lane"):
            await _assignments(provider).move("ghost", LaneRequestReason.UPGRADE)


class _CountingProvider(FakeRedisConnectionProvider):
    """Counts script calls, and can be told to fail them."""

    def __init__(self) -> None:
        super().__init__()
        self.script_calls = 0
        self.failing = False

    def create_client(self, options=None):  # noqa: ANN202
        client = super().create_client(options)
        real = client.evalsha

        async def evalsha(*args, **kwargs):  # noqa: ANN202
            self.script_calls += 1
            if self.failing:
                raise ConnectionError("Redis is down")
            return await real(*args, **kwargs)

        client.evalsha = evalsha
        return client


class TestCache:
    async def test_a_cached_lane_costs_no_redis_call(self) -> None:
        provider = _CountingProvider()
        assignments = _assignments(provider)
        lane = await assignments.lane_for("c-1")
        calls = provider.script_calls

        assert await assignments.lane_for("c-1") == lane
        assert provider.script_calls == calls

    async def test_an_expired_entry_costs_one_lookup(self) -> None:
        provider = _CountingProvider()
        assignments = _assignments(provider, cache_seconds=0)
        await assignments.lane_for("c-1")
        calls = provider.script_calls

        await assignments.lane_for("c-1")

        assert provider.script_calls == calls + 1

    async def test_a_stale_cached_lane_is_used_when_redis_cannot_answer(self) -> None:
        provider = _CountingProvider()
        assignments = _assignments(provider, cache_seconds=0)
        lane = await assignments.lane_for("c-1")
        provider.failing = True

        assert await assignments.lane_for("c-1") == lane

    async def test_with_nothing_cached_a_failed_lookup_is_the_callers_to_handle(self) -> None:
        provider = _CountingProvider()
        provider.failing = True

        with pytest.raises(ConnectionError):
            await _assignments(provider).lane_for("c-1")

    async def test_a_script_redis_forgot_is_loaded_again(self) -> None:
        provider = FakeRedisConnectionProvider()
        assignments = _assignments(provider, cache_seconds=0)
        await assignments.lane_for("c-1")
        await provider.get_client().script_flush()
        loads = len(provider.load_script_calls)

        await assignments.lane_for("c-2")

        assert "c-2" in await _map(provider)
        assert len(provider.load_script_calls) > loads


def _fallbacks(reason: str) -> float:
    prefix = f'pipeshub_lane_assignment_fallbacks_total{{reason="{reason}"}} '
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith(prefix):
            return float(line.removeprefix(prefix))
    return 0.0


class TestRouter:
    async def test_events_go_to_the_assigned_lane(self) -> None:
        provider = FakeRedisConnectionProvider()
        gitlab, slack = _colliding(2)
        router = AssignedRedisLaneRouter(_assignments(provider), logging.getLogger("t"))

        gitlab_topic, _ = await router.place(TOPIC, gitlab)
        slack_topic, slack_key = await router.place(TOPIC, slack)

        assert gitlab_topic != slack_topic
        assert slack_topic == f"{TOPIC}.{(await _map(provider))[slack].lane}"
        assert slack_key == slack

    async def test_when_redis_cannot_answer_events_go_to_the_hash_lane_and_are_counted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = _CountingProvider()
        provider.failing = True
        router = AssignedRedisLaneRouter(_assignments(provider), logging.getLogger("t"))
        before = (_fallbacks("error"), _fallbacks("held"))

        with caplog.at_level(logging.WARNING):
            topics = [(await router.place(TOPIC, "gitlab-1"))[0] for _ in range(3)]

        assert set(topics) == {f"{TOPIC}.{stable_lane('gitlab-1', LANES)}"}
        assert (_fallbacks("error"), _fallbacks("held")) == (before[0] + 1, before[1] + 2)
        warnings = [r for r in caplog.records if "hashed lane" in r.getMessage()]
        assert len(warnings) == 1, "one warning per connector per minute, not per event"

    async def test_after_a_failed_lookup_others_fall_back_without_waiting_on_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _CountingProvider()
        provider.failing = True
        assignments = _assignments(provider)
        router = AssignedRedisLaneRouter(assignments, logging.getLogger("t"))
        await router.place(TOPIC, "gitlab-1")
        calls = provider.script_calls

        topic, _ = await router.place(TOPIC, "slack-1")

        assert topic == f"{TOPIC}.{stable_lane('slack-1', LANES)}"
        assert provider.script_calls == calls, "no Redis call while the hold lasts"
        monkeypatch.setattr(assignments, "_unavailable_until", 0.0)
        provider.failing = False
        assert await router.place(TOPIC, "slack-1") == (
            f"{TOPIC}.{(await _map(provider))['slack-1'].lane}",
            "slack-1",
        )

    async def test_other_laned_topics_keep_hashing(self) -> None:
        router = AssignedRedisLaneRouter(
            _assignments(FakeRedisConnectionProvider()), logging.getLogger("t")
        )

        topic, _ = await router.place("other-events", "c-1")

        assert topic == f"other-events.{stable_lane('c-1', LANES)}"


class TestProducer:
    async def test_two_colliding_connectors_are_published_to_different_streams(self) -> None:
        provider = FakeRedisConnectionProvider()
        inner = _RecordingProducer()
        gitlab, slack = _colliding(2)
        producer = LaneAwareProducer(
            logging.getLogger("t"),
            inner,
            AssignedRedisLaneRouter(_assignments(provider), logging.getLogger("t")),
            LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED),
        )

        await producer.send_messages(
            TOPIC,
            [
                (f"{c}-r{i}", {"eventType": "newRecord", "payload": {"recordId": f"r{i}", "connectorId": c}})
                for c in (gitlab, slack)
                for i in range(3)
            ],
        )

        streams = {topic: len(batch) for topic, batch in inner.batches}
        assert len(streams) == 2
        assert set(streams.values()) == {3}

    async def test_an_upload_is_placed_as_a_knowledge_base(self) -> None:
        provider = FakeRedisConnectionProvider()
        producer = LaneAwareProducer(
            logging.getLogger("t"),
            _RecordingProducer(),
            AssignedRedisLaneRouter(_assignments(provider), logging.getLogger("t")),
            LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED),
        )

        await producer.send_event(
            TOPIC, "newRecord", {"recordId": "r1", "connectorId": "kb-1", "origin": "UPLOAD"}
        )

        assert (await _map(provider))["kb-1"].connector_class == "kb"


class TestFactory:
    @pytest.fixture(autouse=True)
    def _fresh_shared_maps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(assignment_module, "_shared", {})

    def _router(self, producer: object) -> object:
        assert isinstance(producer, LaneAwareProducer)
        return producer._router

    def test_hashing_unless_assignment_is_switched_on(self) -> None:
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            RedisStreamsConfig(),
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(lane_count=LANES),
        )

        assert type(self._router(producer)) is RedisLaneRouter

    def test_redis_with_assignment_on_uses_the_lane_map(self) -> None:
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            RedisStreamsConfig(),
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED),
        )

        assert isinstance(self._router(producer), AssignedRedisLaneRouter)

    def test_kafka_places_by_key_whatever_the_setting(self) -> None:
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            KafkaProducerConfig(bootstrap_servers=["b:9092"], client_id="p"),
            MessageBrokerType.KAFKA,
            lane_config=LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED),
        )

        assert isinstance(self._router(producer), KafkaLaneRouter)

    def test_every_producer_in_a_process_shares_one_cache(self) -> None:
        config = LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED)
        routers = [
            self._router(
                MessagingFactory.create_producer(
                    logging.getLogger("t"), RedisStreamsConfig(), MessageBrokerType.REDIS, lane_config=config
                )
            )
            for _ in range(3)
        ]

        assert len({id(router.assignments) for router in routers}) == 1  # type: ignore[attr-defined]

    def test_record_events_is_assigned_wherever_it_is_listed(self) -> None:
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            RedisStreamsConfig(),
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(
                lane_count=LANES,
                assignment=LaneAssignmentMode.ASSIGNED,
                laned_topics=("entity-events", "record-events"),
            ),
        )

        router = self._router(producer)
        assert isinstance(router, AssignedRedisLaneRouter)
        assert router.assignments.topic == "record-events"

    @pytest.mark.parametrize("laned_topics", [("entity-events",), ()])
    def test_without_record_events_laned_there_is_nothing_to_assign(
        self, laned_topics: tuple[str, ...]
    ) -> None:
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            RedisStreamsConfig(),
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(
                lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED, laned_topics=laned_topics
            ),
        )

        assert type(self._router(producer)) is RedisLaneRouter

    def test_the_lane_rule_comes_from_the_edition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import edition_services

        def edition_rule(_request, _snapshot):  # noqa: ANN202
            return 0

        monkeypatch.setattr(edition_services, "choose_connector_lane", edition_rule)
        producer = MessagingFactory.create_producer(
            logging.getLogger("t"),
            RedisStreamsConfig(),
            MessageBrokerType.REDIS,
            lane_config=LaneConfig(lane_count=LANES, assignment=LaneAssignmentMode.ASSIGNED),
        )

        assert self._router(producer).assignments._choose is edition_rule  # type: ignore[attr-defined]

