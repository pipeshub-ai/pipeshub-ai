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

    def add(self, connector_id: str, *, queued: int = 0, scope: str = "team", kind: str = "SLACK") -> None:
        self.apps[connector_id] = {
            "_key": connector_id,
            "name": connector_id.title(),
            "type": kind,
            "scope": scope,
            "orgId": "org-1",
        }
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
            rows = list(self.queued.get(filters["connectorId"], []))
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
        queued = len(self.queued.get(connector_id, []))
        return {"success": True, "data": {"stats": {"indexingStatus": {"QUEUED": queued}}}}


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
) -> LaneReport:
    return await run_lane_upkeep(
        assignments=_assignments(provider),
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
