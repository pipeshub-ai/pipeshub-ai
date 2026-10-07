"""The indexing service's once-a-minute lane upkeep.

The lane map is the real one, scripts and all, on fakeredis; the graph is a
small fake that answers the two reads upkeep makes (the connectors, and one
connector by id).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

pytest.importorskip("fakeredis.aioredis")
pytest.importorskip("lupa")

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

TOPIC = "record-events"
LANES = 8


class _Graph:
    """The connectors, as upkeep reads them."""

    def __init__(self) -> None:
        self.apps: dict[str, dict[str, Any]] = {}
        self.fail_apps = False
        # Connectors a paged scan of apps misses although they exist.
        self.hidden_from_scan: set[str] = set()
        self.fail_reads = False

    def add(self, connector_id: str, *, scope: str = "team", kind: str = "SLACK") -> None:
        self.apps[connector_id] = {
            "_key": connector_id,
            "name": connector_id.title(),
            "type": kind,
            "scope": scope,
            "orgId": "org-1",
        }

    async def get_documents_paginated(
        self, collection: str, skip: int = 0, limit: int = 50, **_kwargs: object
    ) -> list[dict[str, Any]]:
        assert collection == "apps", f"upkeep read {collection}"
        if self.fail_apps:
            raise ConnectionError("graph unavailable")
        rows = sorted(
            (d for d in self.apps.values() if d["_key"] not in self.hidden_from_scan),
            key=lambda d: d["_key"],
        )
        return rows[skip : skip + limit]

    async def get_document(self, key: str, collection: str, **_kwargs: object) -> dict[str, Any] | None:
        if self.fail_reads:
            raise ConnectionError("graph unavailable")
        return self.apps.get(key) if collection == "apps" else None


@pytest.fixture
def provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider()


@pytest.fixture
def graph() -> _Graph:
    return _Graph()


def _assignments(provider: FakeRedisConnectionProvider) -> LaneAssignments:
    return LaneAssignments(
        logging.getLogger("test_lane_upkeep"), provider, topic=TOPIC, fallback_lane_count=LANES
    )


async def _upkeep(
    provider: FakeRedisConnectionProvider,
    graph: _Graph,
    backlog: LaneBacklog | None = None,
) -> LaneReport:
    return await run_lane_upkeep(
        assignments=_assignments(provider),
        graph_provider=graph,  # type: ignore[arg-type]
        backlog=backlog if backlog is not None else LaneBacklog(TOPIC, {}),
        logger=logging.getLogger("test_lane_upkeep"),
    )


async def _map(provider: FakeRedisConnectionProvider) -> dict[str, LaneEntry]:
    return await read_lane_map(provider.get_client(), TOPIC)


GITLAB, SLACK = _colliding(2)
SHARED = stable_lane(GITLAB, LANES)


class TestNothingHappensToAnUpgradedInstallAsAWhole:
    async def test_connectors_without_an_entry_are_left_for_their_first_publish(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """Nothing records or moves a connector that has not published yet;
        its first publish after the switch places it by the rule."""
        graph.add(GITLAB)
        graph.add(SLACK)

        await _upkeep(provider, graph)

        assert await _map(provider) == {}

    async def test_a_connector_placed_by_its_first_publish_is_left_where_it_is(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB)
        graph.add(SLACK)
        assignments = _assignments(provider)
        await assignments.lane_for(GITLAB)
        await assignments.lane_for(SLACK)
        before = await _map(provider)
        assert before[SLACK].prev_lane == SHARED, "the second one moved off the shared lane"

        await _upkeep(provider, graph)

        after = await _map(provider)
        assert {c: (e.lane, e.prev_lane) for c, e in after.items()} == {
            c: (e.lane, e.prev_lane) for c, e in before.items()
        }


class TestKeepingTheMapInStepWithTheGraph:
    async def test_a_live_connector_a_paged_scan_missed_keeps_its_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """A removal in a page already read shifts the next page by one."""
        graph.add("slack-1")
        await _assignments(provider).lane_for("slack-1")
        graph.hidden_from_scan = {"slack-1"}

        await _upkeep(provider, graph)

        assert (await _map(provider))["slack-1"].is_live

    async def test_a_connector_whose_read_fails_keeps_its_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        await _assignments(provider).lane_for("gone-or-not")
        graph.fail_reads = True

        await _upkeep(provider, graph)

        assert (await _map(provider))["gone-or-not"].is_live

    async def test_a_class_guessed_on_first_publish_is_corrected(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add("gmail-1", scope="personal", kind="GMAIL")
        await _assignments(provider).lane_for("gmail-1")

        await _upkeep(provider, graph)

        assert (await _map(provider))["gmail-1"].connector_class == "personal"

    async def test_a_connector_that_no_longer_exists_has_its_lane_freed(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add("slack-1")
        assignments = _assignments(provider)
        await assignments.lane_for("slack-1")
        await assignments.lane_for("gone-1")

        await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries["gone-1"].state == "deleted"
        assert entries["slack-1"].is_live

    async def test_nothing_is_freed_when_the_connectors_cannot_be_read(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        await _assignments(provider).lane_for("slack-1")
        graph.fail_apps = True

        await _upkeep(provider, graph)

        assert (await _map(provider))["slack-1"].is_live


class TestTheLaneView:
    async def test_it_says_who_is_on_each_lane_and_how_far_behind_it_is(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB)
        graph.add(SLACK)
        assignments = _assignments(provider)
        await assignments.lane_for(GITLAB)
        await assignments.lane_for(SLACK)
        stream = f"{TOPIC}.{SHARED}"
        backlog = LaneBacklog(TOPIC, {stream: 1.0}, pending={stream: 7})

        report = await _upkeep(provider, graph, backlog=backlog)

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

    async def test_a_deleted_connector_is_counted_on_its_lane_but_not_listed(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB)
        assignments = _assignments(provider)
        await assignments.lane_for(GITLAB)
        await assignments.lane_for("gone-1")
        await assignments.release("gone-1")
        gone_lane = (await _map(provider))["gone-1"].lane

        view = (await _upkeep(provider, graph)).as_dict()

        lanes = {lane["lane"]: lane for lane in view["lanes"]}  # type: ignore[union-attr]
        assert lanes[gone_lane]["deleted"] == 1
        assert "gone-1" not in [c["id"] for c in lanes[gone_lane]["connectors"]]
        assert sum(lane["large"] for lane in lanes.values()) == 1

    async def test_the_lane_metrics_are_published_by_lane_never_by_connector(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB)
        await _assignments(provider).lane_for(GITLAB)
        stream = f"{TOPIC}.{SHARED}"

        await _upkeep(provider, graph, backlog=LaneBacklog(TOPIC, {stream: 1.0}))

        series = METRICS_BACKEND.serialize()
        assert f'pipeshub_indexing_lane_connectors{{lane="{SHARED}",size="large"}} 1.0' in series
        assert f'pipeshub_indexing_lane_oldest_waiting_seconds{{lane="{SHARED}"}}' in series
        assert GITLAB not in series

    async def test_without_the_backlog_it_says_so(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        report = await run_lane_upkeep(
            assignments=_assignments(provider),
            graph_provider=graph,  # type: ignore[arg-type]
            backlog=None,
            logger=logging.getLogger("t"),
        )

        assert report.as_dict()["backlogRead"] is False
