"""The indexing service's once-a-minute lane upkeep and the one-time upgrade fix-up.

The lane map is the real one, scripts and all, on fakeredis; the graph is a
small fake that answers the three reads upkeep makes (connectors, their
queued-record counts, and a connector's queued records).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

from app.modules.indexing import lane_upkeep as lane_upkeep_module
from app.modules.indexing.lane_upkeep import (
    LaneReport,
    last_lane_report,
    run_lane_upkeep,
)
from app.services.messaging.lanes.assignment import (
    LaneAssignments,
    LaneEntry,
    lane_map_key,
    lane_meta_key,
    read_lane_map,
)
from app.services.messaging.lanes.backlog import LaneBacklog
from app.services.messaging.lanes.hash_router import stable_lane
from app.telemetry.backend import METRICS_BACKEND
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.unit.services.messaging.test_lane_assignment import _colliding
from tests.unit.services.messaging.test_lane_aware_producer import _RecordingProducer

TOPIC = "record-events"
LANES = 8


class _Graph:
    """Connectors, and per connector its QUEUED records in queue order."""

    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}
        self.queued: dict[str, list[dict[str, Any]]] = {}
        self.fail_apps = False
        # Connectors a paged scan of apps misses although they exist.
        self.hidden_from_scan: set[str] = set()
        self.fail_reads = False
        self.fail_stats: set[str] = set()
        self.org_edges: dict[str, str] = {}

    def add(
        self,
        connector_id: str,
        *,
        queued: int = 0,
        scope: str = "team",
        kind: str = "SLACK",
        org_on_document: bool = True,
        org_edge: bool = True,
    ) -> None:
        """``org_on_document=False``: a connector from before ``orgId`` was
        stored on its document, whose org is only the org-app edge."""
        self.apps[connector_id] = {
            "_key": connector_id,
            "name": connector_id.title(),
            "type": kind,
            "scope": scope,
            **({"orgId": "org-1"} if org_on_document else {}),
        }
        if org_edge:
            self.org_edges[connector_id] = "org-1"
        self.queued[connector_id] = [
            {
                "_key": f"{connector_id}-r{i}",
                "recordName": f"r{i}",
                "orgId": "org-1",
                "connectorId": connector_id,
                "origin": "CONNECTOR",
                "indexingStatus": "QUEUED",
                "queuedAtTimestamp": 1000 + i,
                "version": 0,
            }
            for i in range(queued)
        ]

    async def get_documents_paginated(
        self, collection, skip=0, limit=50, filters=None, sort_field=None, **_kwargs
    ) -> list[dict[str, Any]]:
        if collection == "apps":
            if self.fail_apps:
                raise ConnectionError("graph unavailable")
            rows = sorted(
                (d for d in self.apps.values() if d["_key"] not in self.hidden_from_scan),
                key=lambda d: d["_key"],
            )
        else:
            rows = [
                r
                for r in self.queued.get(filters["connectorId"], [])
                if r["indexingStatus"] == filters["indexingStatus"]
            ]
            rows.sort(key=lambda r: r[sort_field])
        return rows[skip : skip + limit]

    async def get_document(self, key: str, collection: str, **_kwargs: object) -> dict[str, Any] | None:
        if self.fail_reads:
            raise ConnectionError("graph unavailable")
        return self.apps.get(key) if collection == "apps" else None

    async def get_connector_stats(self, org_id: str, connector_id: str) -> dict[str, Any]:
        if connector_id in self.fail_stats:
            # What both providers return when the aggregation fails.
            return {"success": False, "data": None}
        # The providers count only records of the org they are given, so an
        # unknown org answers with zeros, not an error.
        queued = sum(
            r["orgId"] == org_id and r["indexingStatus"] == "QUEUED"
            for r in self.queued.get(connector_id, [])
        )
        return {"success": True, "data": {"stats": {"indexingStatus": {"QUEUED": queued}}}}

    async def get_edges_to_node(self, node_id: str, edge_collection: str) -> list[dict[str, Any]]:
        connector_id = node_id.rsplit("/", 1)[-1]
        org = self.org_edges.get(connector_id)
        return [{"from_id": org, "to_id": connector_id}] if org else []


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider()


@pytest.fixture
def graph() -> _Graph:
    return _Graph()


@pytest.fixture
def producer() -> _RecordingProducer:
    return _RecordingProducer()


def _assignments(provider: FakeRedisConnectionProvider) -> LaneAssignments:
    return LaneAssignments(
        logging.getLogger("test_lane_upkeep"), provider, topic=TOPIC, fallback_lane_count=LANES
    )


async def _upkeep(
    provider: FakeRedisConnectionProvider,
    graph: _Graph,
    producer: _RecordingProducer,
    backlog: LaneBacklog | None = None,
    rescue_cap: int = 20_000,
    assignments: LaneAssignments | None = None,
) -> LaneReport:
    return await run_lane_upkeep(
        assignments=assignments or _assignments(provider),
        graph_provider=graph,  # type: ignore[arg-type]
        producer=producer,  # type: ignore[arg-type]
        backlog=backlog if backlog is not None else LaneBacklog(TOPIC, {}),
        logger=logging.getLogger("test_lane_upkeep"),
        rescue_cap=rescue_cap,
    )


async def _map(provider: FakeRedisConnectionProvider) -> dict[str, LaneEntry]:
    return await read_lane_map(provider.get_client(), TOPIC)


GITLAB, SLACK = _colliding(2)
SHARED = stable_lane(GITLAB, LANES)


class TestUpgradeFixUp:
    async def test_the_connector_with_the_backlog_keeps_the_lane_and_the_other_moves(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)

        await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries[GITLAB] == LaneEntry(SHARED, "team")
        assert entries[SLACK].lane != SHARED
        assert entries[SLACK].prev_lane == SHARED

    async def test_the_moved_connectors_queued_records_are_re_sent_to_its_new_lane_oldest_first(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)

        await _upkeep(provider, graph, producer)

        new_lane = (await _map(provider))[SLACK].lane
        assert [(topic, payload["recordId"]) for topic, _type, payload, _key in producer.events] == [
            (f"{TOPIC}.{new_lane}", f"{SLACK}-r{i}") for i in range(6)
        ]
        assert all(payload["connectorId"] == SLACK for _t, _e, payload, _k in producer.events)

    async def test_no_more_than_the_cap_is_re_sent(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=30)

        await _upkeep(provider, graph, producer, rescue_cap=5)

        assert [payload["recordId"] for _t, _e, payload, _k in producer.events] == [
            f"{SLACK}-r{i}" for i in range(5)
        ]

    async def test_a_duplicate_parked_behind_its_twin_is_not_re_sent(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=2)
        graph.queued[SLACK][0].update(md5Checksum="abc", virtualRecordId="vr-1")

        await _upkeep(provider, graph, producer)

        assert [payload["recordId"] for _t, _e, payload, _k in producer.events] == [f"{SLACK}-r1"]

    async def test_when_the_smaller_connector_published_first_it_is_the_one_that_moves(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Left to first publishes, Slack kept the shared lane and GitLab, whose
        backlog is on it, moved. GitLab's old events are still on that lane, so
        Slack has to go, taking its queued records with it."""
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        assignments = _assignments(provider)
        await assignments.lane_for(SLACK)
        await assignments.lane_for(GITLAB)
        gitlab_before = (await _map(provider))[GITLAB]
        assert gitlab_before.prev_lane == SHARED

        await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries[GITLAB] == gitlab_before
        assert entries[SLACK].lane not in (SHARED, gitlab_before.lane)
        assert {topic for topic, *_rest in producer.events} == {f"{TOPIC}.{entries[SLACK].lane}"}

    async def test_when_the_smaller_connector_already_moved_off_its_records_follow_it(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        assignments = _assignments(provider)
        await assignments.lane_for(GITLAB)
        await assignments.lane_for(SLACK)
        slack = (await _map(provider))[SLACK]

        await _upkeep(provider, graph, producer)

        assert (await _map(provider))[SLACK] == slack
        assert len(producer.events) == 6
        assert {topic for topic, *_rest in producer.events} == {f"{TOPIC}.{slack.lane}"}

    async def test_connectors_that_never_collided_are_recorded_where_they_are(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        apart = []
        for name in (f"conn-{i}" for i in range(200)):
            if stable_lane(name, LANES) not in {stable_lane(c, LANES) for c in apart}:
                apart.append(name)
            if len(apart) == 4:
                break
        for name in apart:
            graph.add(name, queued=3)

        await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert {c: entries[c] for c in apart} == {c: LaneEntry(stable_lane(c, LANES), "team") for c in apart}
        assert producer.events == []

    async def test_small_connectors_sharing_a_large_ones_lane_are_left_alone(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6, scope="personal", kind="GMAIL")

        await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[SLACK].lane) == (SHARED, SHARED)
        assert entries[SLACK].connector_class == "personal"
        assert producer.events == []

    async def test_it_runs_once(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        first = await _upkeep(provider, graph, producer)
        sent = len(producer.events)

        second = await _upkeep(provider, graph, producer)

        assert len(producer.events) == sent
        assert first.migrated_at_ms is not None
        assert second.migrated_at_ms == first.migrated_at_ms

    async def test_without_the_connectors_it_waits_for_a_pass_that_can_read_them(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.fail_apps = True

        report = await _upkeep(provider, graph, producer)

        assert report.migrated_at_ms is None
        assert await _map(provider) == {}


class TestTheFixUpFinishesOnlyWhenItHasSeparatedThem:
    async def test_a_connector_still_settling_is_moved_on_a_later_pass_and_only_then_is_it_done(
        self,
        provider: FakeRedisConnectionProvider,
        graph: _Graph,
        producer: _RecordingProducer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A and B share a hash lane; C, with the biggest queue, hashes to the
        lane the rule gives A when A publishes after B. A must leave C's lane
        but is still settling the move off its hash lane."""
        monkeypatch.setenv("FAIR_SCHEDULING_LANE_CACHE_SECONDS", "0")
        monkeypatch.setattr(lane_upkeep_module, "_FENCE_MARGIN_MS", 0)
        a, b = _colliding(2, prefix="pair")
        shared = stable_lane(a, LANES)
        target = 0 if shared else 1
        c = next(x for x in (f"solo-{i}" for i in range(500)) if stable_lane(x, LANES) == target)
        graph.add(a, queued=5)
        graph.add(b, queued=10)
        graph.add(c, queued=40)
        assignments = _assignments(provider)
        await assignments.lane_for(b)
        await assignments.lane_for(a)
        a_entry = (await _map(provider))[a]
        assert (a_entry.lane, a_entry.prev_lane) == (target, shared)

        first = await _upkeep(provider, graph, producer)

        assert first.migrated_at_ms is None
        assert (await _map(provider))[a].lane == target, "A could not leave yet"
        assert f"{TOPIC}.{target}" not in {t for t, *_ in producer.events}, (
            "nothing re-sent onto the lane A still shares with C"
        )

        second = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries[c].lane == target
        assert entries[a].lane not in (target, shared)
        assert {t for t, *_ in producer.events} == {f"{TOPIC}.{entries[a].lane}"}
        assert second.migrated_at_ms is not None

    async def test_an_unknown_queue_count_keeps_everyone_where_they_are_until_it_is_known(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        graph.fail_stats = {GITLAB}

        first = await _upkeep(provider, graph, producer)

        assert first.migrated_at_ms is None
        assert {e.lane for e in (await _map(provider)).values()} == {SHARED}
        assert producer.events == []

        graph.fail_stats = set()
        second = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[SLACK].prev_lane) == (SHARED, SHARED)
        assert entries[SLACK].lane != SHARED
        assert second.migrated_at_ms is not None

    async def test_a_re_send_that_stopped_short_is_finished_next_pass_and_nothing_is_decided_twice(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=4)
        send = producer.send_event
        calls = 0

        async def fail_once_at_the_third(**kwargs: object) -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ConnectionError("broker blip")
            return await send(**kwargs)

        producer.send_event = fail_once_at_the_third  # type: ignore[method-assign]
        first = await _upkeep(provider, graph, producer)
        slack_lane = (await _map(provider))[SLACK].lane
        graph.queued[GITLAB] = []  # GitLab drained since: it still keeps its lane

        second = await _upkeep(provider, graph, producer)

        assert first.migrated_at_ms is None
        assert second.migrated_at_ms is not None
        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[SLACK].lane) == (SHARED, slack_lane)
        assert {t for t, *_ in producer.events} == {f"{TOPIC}.{slack_lane}"}


class TestAMoveIsOnlyMadeToALaneOfItsOwn:
    async def _eight_lanes_each_with_a_team_connector(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> tuple[str, str, int]:
        """Slack hashes to lane 0 but sits on lane 1 with GitLab; every lane
        already has a large connector."""
        client = provider.get_client()
        slack = next(c for c in (f"slack-{i}" for i in range(500)) if stable_lane(c, LANES) == 0)
        gitlab = "gitlab-big"
        mapping = {slack: LaneEntry(1, "team").encode(), gitlab: LaneEntry(1, "team").encode()}
        graph.add(slack, queued=50)
        graph.add(gitlab, queued=200)
        for lane in (0, *range(2, LANES)):
            other = f"team-on-{lane}"
            mapping[other] = LaneEntry(lane, "team").encode()
            graph.add(other, queued=10)
        await client.hset(lane_map_key(TOPIC), mapping=mapping)
        counts = {f"large:{lane}": "1" for lane in range(LANES)} | {"large:1": "2", "laneCount": str(LANES)}
        await client.hset(lane_meta_key(TOPIC), mapping=counts)
        return slack, gitlab, 1

    async def test_with_no_lane_free_the_connector_stays_and_the_fix_up_is_done(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        slack, _gitlab, shared = await self._eight_lanes_each_with_a_team_connector(provider, graph)

        report = await _upkeep(provider, graph, producer)

        assert (await _map(provider))[slack] == LaneEntry(shared, "team")
        assert producer.events == []
        assert report.migrated_at_ms is not None

    async def test_a_lane_an_edition_rule_picks_is_its_decision_and_the_records_follow(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """An edition may put a connector beside another large one; the fix-up
        takes that as decided and re-sends its queued records there."""
        await provider.get_client().hset(lane_meta_key(TOPIC), "laneCount", str(LANES))
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        graph.add("drive-1", queued=3)
        await _assignments(provider).record_at_hash_lane("drive-1", "team")
        drive_lane = stable_lane("drive-1", LANES)
        assert drive_lane != SHARED
        onto_drive = LaneAssignments(
            logging.getLogger("t"),
            provider,
            topic=TOPIC,
            fallback_lane_count=LANES,
            choose=lambda request, _snapshot: drive_lane
            if request.connector_id == SLACK
            else request.current_lane,
        )

        report = await _upkeep(provider, graph, producer, assignments=onto_drive)

        assert (await _map(provider))[SLACK].lane == drive_lane
        assert [(t, p["recordId"]) for t, _e, p, _k in producer.events] == [
            (f"{TOPIC}.{drive_lane}", f"{SLACK}-r{i}") for i in range(6)
        ]
        assert report.migrated_at_ms is not None


async def _seed(
    provider: FakeRedisConnectionProvider,
    graph: _Graph,
    placements: dict[str, tuple[int, int | None, int]],
) -> None:
    """Team connectors already in the map: id -> (lane, prevLane, queued)."""
    client = provider.get_client()
    counts: dict[str, int] = {}
    mapping = {}
    for connector_id, (lane, prev, queued) in placements.items():
        graph.add(connector_id, queued=queued)
        mapping[connector_id] = LaneEntry(
            lane, "team", prev_lane=prev, moved_at_ms=1 if prev is not None else None
        ).encode()
        counts[f"large:{lane}"] = counts.get(f"large:{lane}", 0) + 1
    await client.hset(lane_map_key(TOPIC), mapping=mapping)
    await client.hset(
        lane_meta_key(TOPIC), mapping={**{k: str(v) for k, v in counts.items()}, "laneCount": str(LANES)}
    )


class TestTheBiggestQueueGetsTheFreeLane:
    async def test_the_connector_with_the_larger_queue_takes_the_only_free_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Lane 0 holds 30 and 10 queued, lane 6 holds 200 and 50, lane 7 is
        empty: the 50 must not wait behind the 200 because lane 0 came first."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "a-30": (0, None, 30),
            "b-10": (0, None, 10),
            "c-200": (6, None, 200),
            "d-50": (6, None, 50),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(1, 6)}
        await _seed(provider, graph, placements)

        report = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries["d-50"].lane == 7
        assert {payload["connectorId"] for _t, _e, payload, _k in producer.events} == {"d-50"}
        assert (entries["a-30"].lane, entries["b-10"].lane, entries["c-200"].lane) == (0, 0, 6)
        assert report.migrated_at_ms is not None

    async def test_one_already_moved_onto_a_shared_lane_stays_and_its_records_follow(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Every lane has a team connector, lane 0 two of them, and X moved off
        lane 0 onto lane 1 on its first publish: nowhere is free to go."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "keeper-0": (0, None, 100),
            "second-0": (0, None, 5),
            "x-moved": (1, 0, 20),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(1, LANES)}
        await _seed(provider, graph, placements)

        report = await _upkeep(provider, graph, producer)

        assert report.migrated_at_ms is not None
        entries = await _map(provider)
        assert (entries["x-moved"].lane, entries["second-0"].lane) == (1, 0)
        assert [(t, p["recordId"]) for t, _e, p, _k in producer.events] == [
            (f"{TOPIC}.1", f"x-moved-r{i}") for i in range(20)
        ], "its new events already go to lane 1, so its queued ones follow"


class TestAFreeLaneReallyIsFree:
    async def test_a_lane_still_holding_a_keepers_backlog_is_not_given_away(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Lane 0: an occupant with 50 queued and K (200), which moved to lane
        2 but whose backlog is still on lane 0. Lane 1: 100 and 10. Lane 7 is
        the only free lane."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "o-50": (0, None, 50),
            "k-200": (2, 0, 200),
            "p-100": (1, None, 100),
            "q-10": (1, None, 10),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(3, 7)}
        await _seed(provider, graph, placements)

        report = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries["o-50"].lane == 7
        assert entries["q-10"].lane == 1, "lane 0 still holds K's backlog"
        # K keeps lane 0's backlog, where O no longer sits in front of it;
        # only O's records follow O.
        assert {t for t, *_ in producer.events} == {f"{TOPIC}.7"}
        assert report.migrated_at_ms is not None

    async def test_a_connector_still_settling_holds_the_free_lane_for_itself(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """One free lane; A (50) has to leave a shared lane but is still
        settling an earlier move; B (10) shares another lane. B must not take
        the lane A is waiting for."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "c-200": (3, None, 200),
            "a-50": (3, 4, 50),
            "big-4": (4, None, 300),
            "x-100": (0, None, 100),
            "b-10": (0, None, 10),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in (1, 2, 5, 6)}
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert first.migrated_at_ms is None
        assert (entries["a-50"].lane, entries["b-10"].lane) == (3, 0)

        settled = LaneEntry(3, "team")
        await provider.get_client().hset(lane_map_key(TOPIC), "a-50", settled.encode())
        second = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries["a-50"].lane == 7
        assert entries["b-10"].lane == 0
        assert second.migrated_at_ms is not None


    async def _settle(self, provider: FakeRedisConnectionProvider, connector_id: str) -> None:
        """Its earlier move has settled: upkeep cleared its previous lane."""
        entry = (await _map(provider))[connector_id]
        settled = LaneEntry(entry.lane, entry.connector_class)
        await provider.get_client().hset(lane_map_key(TOPIC), connector_id, settled.encode())

    async def test_its_current_lane_is_decided_before_its_records_are_re_sent(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Lane 0: GitLab (100). Slack (40) moved off lane 0 onto lane 1,
        beside Drive (80). Lane 7 is free. Slack must end on lane 7 with its
        records there, not have them re-sent behind Drive."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "gitlab-100": (0, None, 100),
            "slack-40": (1, 0, 40),
            "drive-80": (1, None, 80),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(2, 7)}
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph, producer)

        assert first.migrated_at_ms is None
        assert producer.events == [], "nothing re-sent onto the lane it still has to leave"
        await self._settle(provider, "slack-40")
        second = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert (entries["slack-40"].lane, entries["drive-80"].lane) == (7, 1)
        assert {t for t, *_ in producer.events} == {f"{TOPIC}.7"}
        assert len(producer.events) == 40
        assert second.migrated_at_ms is not None

    async def test_a_held_lane_is_held_even_with_another_lane_free(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Lanes 6 and 7 free; A (50) still settling on a shared lane; B (30)
        and C (10) each share another lane. A, with the most waiting, holds
        lane 6, so only B may take a free lane."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "k0-100": (0, None, 100),
            "b-30": (0, None, 30),
            "k1-100": (1, None, 100),
            "c-10": (1, None, 10),
            "k2-200": (2, None, 200),
            "a-50": (2, 3, 50),
            "big-3": (3, None, 300),
            "solo-4": (4, None, 5),
            "solo-5": (5, None, 5),
        }
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert first.migrated_at_ms is None
        assert (entries["b-30"].lane, entries["c-10"].lane, entries["a-50"].lane) == (7, 1, 2)
        assert not any(e.lane == 6 for e in entries.values()), "lane 6 is held for A"

        await self._settle(provider, "a-50")
        second = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert (entries["a-50"].lane, entries["c-10"].lane) == (6, 1)
        assert second.migrated_at_ms is not None

    async def test_a_connector_recorded_late_beside_one_already_moved_is_separated(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """A connector the fix-up moved on an earlier pass still takes part
        on the lane it is on now: one recorded there later must not just join it."""
        await _seed(provider, graph, {"slack-moved": (5, None, 20)})
        await _assignments(provider).note_fix_up_progress("slack-moved", "rescued")
        newcomer = next(c for c in (f"late-{i}" for i in range(500)) if stable_lane(c, LANES) == 5)
        graph.add(newcomer, queued=50)

        report = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries[newcomer].lane != entries["slack-moved"].lane
        assert report.migrated_at_ms is not None


class TestConnectorsFromBeforeOrgWasOnTheDocument:
    async def test_the_org_comes_from_the_edge_so_the_backlog_keeps_the_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        # The backlog is on the connector whose id sorts last, so a tie of
        # zeros (an unread org) would give the lane to the other one.
        keeper, mover = max(GITLAB, SLACK), min(GITLAB, SLACK)
        graph.add(keeper, queued=40, org_on_document=False)
        graph.add(mover, queued=6, org_on_document=False)

        report = await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries[keeper].lane == SHARED
        assert entries[mover].lane != SHARED
        assert report.migrated_at_ms is not None

    async def test_with_no_org_to_be_found_nobody_moves_and_it_tries_again_later(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40, org_on_document=False, org_edge=False)
        graph.add(SLACK, queued=6, org_on_document=False, org_edge=False)

        report = await _upkeep(provider, graph, producer)

        assert {e.lane for e in (await _map(provider)).values()} == {SHARED}
        assert producer.events == []
        assert report.migrated_at_ms is None


class TestTheReSendMissesNothingWhileTheConsumerWorks:
    async def test_a_record_that_slid_into_a_page_already_read_is_still_re_sent(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """Offset paging while records leave QUEUED: one finishing in the first
        page moves the first record of the second page into the first."""
        graph.add(GITLAB, queued=2_000)
        graph.add(SLACK, queued=1_000)
        send = producer.send_event
        sends = 0

        async def a_record_finishes_meanwhile(**kwargs: object) -> bool:
            nonlocal sends
            sends += 1
            if sends == 5:
                graph.queued[SLACK][2]["indexingStatus"] = "COMPLETED"
            return await send(**kwargs)

        producer.send_event = a_record_finishes_meanwhile  # type: ignore[method-assign]

        report = await _upkeep(provider, graph, producer)

        re_sent = {payload["recordId"] for _t, _e, payload, _k in producer.events}
        assert re_sent >= {f"{SLACK}-r{i}" for i in range(1_000) if i != 2}
        assert len(producer.events) == len(re_sent), "nothing sent twice"
        assert report.migrated_at_ms is not None


    async def test_past_the_cap_the_oldest_records_are_all_re_sent_while_others_finish(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """25,000 queued and a cap of 20,000, with the consumer finishing
        records all the while: every one of the oldest 20,000 is re-sent."""
        graph.add(GITLAB, queued=30_000)
        graph.add(SLACK, queued=25_000)
        oldest = {f"{SLACK}-r{i}" for i in range(20_000)}
        send = producer.send_event
        sends = 0

        async def records_finish_meanwhile(**kwargs: object) -> bool:
            nonlocal sends
            sends += 1
            if sends % 500 == 0:
                graph.queued[SLACK][sends + 3]["indexingStatus"] = "COMPLETED"
            return await send(**kwargs)

        producer.send_event = records_finish_meanwhile  # type: ignore[method-assign]

        report = await _upkeep(provider, graph, producer, rescue_cap=20_000)

        re_sent = [payload["recordId"] for _t, _e, payload, _k in producer.events]
        assert set(re_sent) == oldest
        assert len(re_sent) == len(oldest), "nothing sent twice"
        assert report.migrated_at_ms is not None


class TestKeepingTheMapInStepWithTheGraph:
    async def test_a_live_connector_a_paged_scan_missed_keeps_its_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        """A removal in a page already read shifts the next page by one."""
        graph.add("slack-1")
        await _assignments(provider).lane_for("slack-1")
        graph.hidden_from_scan = {"slack-1"}

        await _upkeep(provider, graph, producer)

        assert (await _map(provider))["slack-1"].is_live

    async def test_a_connector_whose_read_fails_keeps_its_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        await _assignments(provider).lane_for("gone-or-not")
        graph.fail_reads = True

        await _upkeep(provider, graph, producer)

        assert (await _map(provider))["gone-or-not"].is_live
    async def test_a_class_guessed_on_first_publish_is_corrected(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add("gmail-1", scope="personal", kind="GMAIL")
        await _assignments(provider).lane_for("gmail-1")

        await _upkeep(provider, graph, producer)

        assert (await _map(provider))["gmail-1"].connector_class == "personal"

    async def test_a_connector_that_no_longer_exists_has_its_lane_freed(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add("slack-1")
        assignments = _assignments(provider)
        await assignments.lane_for("slack-1")
        await assignments.lane_for("gone-1")

        await _upkeep(provider, graph, producer)

        entries = await _map(provider)
        assert entries["gone-1"].state == "deleted"
        assert entries["slack-1"].is_live

    async def test_nothing_is_freed_when_the_connectors_cannot_be_read(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        await _assignments(provider).lane_for("slack-1")
        graph.fail_apps = True

        await _upkeep(provider, graph, producer)

        assert (await _map(provider))["slack-1"].is_live


class TestTheLaneView:
    async def test_it_says_who_is_on_each_lane_and_how_far_behind_it_is(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        stream = f"{TOPIC}.{SHARED}"
        backlog = LaneBacklog(TOPIC, {stream: 1.0}, pending={stream: 7})

        report = await _upkeep(provider, graph, producer, backlog=backlog)

        assert report is last_lane_report(TOPIC)
        view = report.as_dict()
        assert view["laneCount"] == LANES
        lanes = {lane["lane"]: lane for lane in view["lanes"]}  # type: ignore[union-attr]
        shared = lanes[SHARED]
        assert shared["stream"] == stream
        assert shared["pending"] == 7
        assert shared["oldestWaitingSeconds"] > 0
        assert [c["id"] for c in shared["connectors"]] == [GITLAB]
        assert shared["connectors"][0]["name"] == GITLAB.title()
        slack_lane = (await _map(provider))[SLACK].lane
        assert shared["movingOff"] == [{"id": SLACK, "toLane": slack_lane, "fencedAt": None}]
        assert lanes[slack_lane]["movingOn"] == [{"id": SLACK, "fromLane": SHARED, "fencedAt": None}]

    async def test_the_lane_metrics_are_published_by_lane_never_by_connector(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        graph.add(GITLAB, queued=40)
        stream = f"{TOPIC}.{SHARED}"

        await _upkeep(provider, graph, producer, backlog=LaneBacklog(TOPIC, {stream: 1.0}))

        series = METRICS_BACKEND.serialize()
        assert f'pipeshub_indexing_lane_connectors{{lane="{SHARED}",size="large"}} 1.0' in series
        assert f'pipeshub_indexing_lane_oldest_waiting_seconds{{lane="{SHARED}"}}' in series
        assert GITLAB not in series

    async def test_without_the_backlog_it_says_so(
        self, provider: FakeRedisConnectionProvider, graph: _Graph, producer: _RecordingProducer
    ) -> None:
        report = await run_lane_upkeep(
            assignments=_assignments(provider),
            graph_provider=graph,  # type: ignore[arg-type]
            producer=producer,  # type: ignore[arg-type]
            backlog=None,
            logger=logging.getLogger("t"),
        )

        assert report.as_dict()["backlogRead"] is False
