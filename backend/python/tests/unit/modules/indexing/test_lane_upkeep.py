"""The indexing service's once-a-minute lane upkeep and the one-time upgrade fix-up.

The lane map is the real one, scripts and all, on fakeredis; the graph is a
small fake that answers the reads upkeep makes (connectors, a connector by
id, its org and its queued-record count).
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
    lane_map_key,
    lane_meta_key,
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


def _assignments(provider: FakeRedisConnectionProvider) -> LaneAssignments:
    return LaneAssignments(
        logging.getLogger("test_lane_upkeep"), provider, topic=TOPIC, fallback_lane_count=LANES
    )


async def _upkeep(
    provider: FakeRedisConnectionProvider,
    graph: _Graph,
    backlog: LaneBacklog | None = None,
    assignments: LaneAssignments | None = None,
) -> LaneReport:
    return await run_lane_upkeep(
        assignments=assignments or _assignments(provider),
        graph_provider=graph,  # type: ignore[arg-type]
        backlog=backlog if backlog is not None else LaneBacklog(TOPIC, {}),
        logger=logging.getLogger("test_lane_upkeep"),
    )


async def _map(provider: FakeRedisConnectionProvider) -> dict[str, LaneEntry]:
    return await read_lane_map(provider.get_client(), TOPIC)


async def _settle(provider: FakeRedisConnectionProvider, connector_id: str) -> None:
    """Its earlier move has settled: upkeep cleared its previous lane."""
    entry = (await _map(provider))[connector_id]
    settled = LaneEntry(entry.lane, entry.connector_class)
    await provider.get_client().hset(lane_map_key(TOPIC), connector_id, settled.encode())


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


def _lanes(entries: dict[str, LaneEntry], *connector_ids: str) -> tuple[int, ...]:
    return tuple(entries[c].lane for c in connector_ids)


GITLAB, SLACK = _colliding(2)
SHARED = stable_lane(GITLAB, LANES)


class TestUpgradeFixUp:
    async def test_the_connector_with_the_backlog_keeps_the_lane_and_the_other_moves(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)

        first = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries[GITLAB] == LaneEntry(SHARED, "team")
        assert entries[SLACK].lane != SHARED
        assert entries[SLACK].prev_lane == SHARED
        assert first.migrated_at_ms is None, "a pass that moves someone leaves the rest to the next"
        assert (await _upkeep(provider, graph)).migrated_at_ms is not None

    async def test_its_queued_records_are_left_where_they_are(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """They are worked off on the old lane, which the sweep keeps counting
        while the move settles; none is re-published."""
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        read = graph.get_documents_paginated
        collections: list[str] = []

        async def reading(collection: str, *args: object, **kwargs: object) -> list[dict[str, Any]]:
            collections.append(collection)
            return await read(collection, *args, **kwargs)

        graph.get_documents_paginated = reading  # type: ignore[method-assign]

        await _upkeep(provider, graph)
        await _upkeep(provider, graph)

        assert (await _map(provider))[SLACK].prev_lane == SHARED
        assert set(collections) == {"apps"}, "no record was read to be re-sent"

    async def test_when_the_one_with_the_backlog_already_moved_off_the_other_one_moves(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """Left to first publishes, Slack kept the shared lane and GitLab moved,
        but GitLab's backlog is still on the shared lane. Slack has to go, onto
        a lane of its own; GitLab stays where its first publish put it."""
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        assignments = _assignments(provider)
        await assignments.lane_for(SLACK)
        await assignments.lane_for(GITLAB)
        gitlab = (await _map(provider))[GITLAB]
        assert gitlab.prev_lane == SHARED

        first = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries[GITLAB] == gitlab
        assert entries[SLACK].lane not in (SHARED, gitlab.lane)
        assert first.migrated_at_ms is None
        assert (await _upkeep(provider, graph)).migrated_at_ms is not None

    async def test_a_connector_that_already_moved_off_stays_where_it_is(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        assignments = _assignments(provider)
        await assignments.lane_for(GITLAB)
        await assignments.lane_for(SLACK)
        slack = (await _map(provider))[SLACK]

        report = await _upkeep(provider, graph)

        assert (await _map(provider))[SLACK] == slack
        assert report.migrated_at_ms is not None

    async def test_connectors_that_never_collided_are_recorded_where_they_are(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        apart = []
        for name in (f"conn-{i}" for i in range(200)):
            if stable_lane(name, LANES) not in {stable_lane(c, LANES) for c in apart}:
                apart.append(name)
            if len(apart) == 4:
                break
        for name in apart:
            graph.add(name, queued=3)

        report = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert {c: entries[c] for c in apart} == {c: LaneEntry(stable_lane(c, LANES), "team") for c in apart}
        assert report.migrated_at_ms is not None

    async def test_small_connectors_sharing_a_large_ones_lane_are_left_alone(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6, scope="personal", kind="GMAIL")

        report = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[SLACK].lane) == (SHARED, SHARED)
        assert entries[SLACK].connector_class == "personal"
        assert report.migrated_at_ms is not None

    async def test_once_done_it_does_not_run_again(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        await _upkeep(provider, graph)
        done = await _upkeep(provider, graph)
        assert done.migrated_at_ms is not None
        late = next(c for c in (f"late-{i}" for i in range(500)) if stable_lane(c, LANES) == SHARED)
        await _assignments(provider).record_at_hash_lane(late, "team")
        graph.add(late, queued=90)

        again = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[late].lane) == (SHARED, SHARED)
        assert again.migrated_at_ms == done.migrated_at_ms

    async def test_without_the_connectors_it_waits_for_a_pass_that_can_read_them(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.fail_apps = True

        report = await _upkeep(provider, graph)

        assert report.migrated_at_ms is None
        assert await _map(provider) == {}


class TestOneMovePerPass:
    async def test_the_connector_with_the_larger_queue_moves_first(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
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

        first = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert _lanes(entries, "a-30", "b-10", "c-200", "d-50") == (0, 0, 6, 7)
        assert first.migrated_at_ms is None

        second = await _upkeep(provider, graph)

        assert second.migrated_at_ms is None, "lane 6 still drains D's backlog and may free up"
        await _settle(provider, "d-50")
        third = await _upkeep(provider, graph)

        assert _lanes(await _map(provider), "a-30", "b-10") == (0, 0)
        assert third.migrated_at_ms is not None, "no lane is left to split onto"

    async def test_two_shared_lanes_and_two_free_lanes_are_split_on_two_passes(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        placements: dict[str, tuple[int, int | None, int]] = {
            "k0-100": (0, None, 100),
            "b-30": (0, None, 30),
            "k1-100": (1, None, 100),
            "c-10": (1, None, 10),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(2, 6)}
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries["b-30"].lane in (6, 7)
        assert entries["c-10"].lane == 1
        assert first.migrated_at_ms is None

        second = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert {entries["b-30"].lane, entries["c-10"].lane} == {6, 7}
        assert second.migrated_at_ms is None
        assert (await _upkeep(provider, graph)).migrated_at_ms is not None

    async def test_a_connector_recorded_late_beside_one_already_moved_is_separated(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        await _seed(provider, graph, {"slack-moved": (5, None, 20)})
        newcomer = next(c for c in (f"late-{i}" for i in range(500)) if stable_lane(c, LANES) == 5)
        graph.add(newcomer, queued=50)

        await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries[newcomer].lane == 5, "the larger queue keeps the lane"
        assert entries["slack-moved"].lane != 5
        assert (await _upkeep(provider, graph)).migrated_at_ms is not None


class TestOnlyOntoALaneThatIsTrulyEmpty:
    async def test_a_lane_still_holding_a_moved_connectors_backlog_is_not_used(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """K (200) moved from lane 0 to lane 2 and its backlog is still on
        lane 0, where nobody sits now. Lane 1 holds 100 and 10. Lane 7 is the
        only lane with nothing on it."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "k-200": (2, 0, 200),
            "p-100": (1, None, 100),
            "q-10": (1, None, 10),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(3, 7)}
        await _seed(provider, graph, placements)

        await _upkeep(provider, graph)

        assert (await _map(provider))["q-10"].lane == 7

    async def test_with_no_lane_free_the_connector_stays_and_the_fix_up_is_done(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        placements: dict[str, tuple[int, int | None, int]] = {
            "gitlab-200": (1, None, 200),
            "slack-50": (1, None, 50),
        }
        placements |= {f"solo-{lane}": (lane, None, 10) for lane in (0, *range(2, LANES))}
        await _seed(provider, graph, placements)

        report = await _upkeep(provider, graph)

        assert (await _map(provider))["slack-50"] == LaneEntry(1, "team")
        assert report.migrated_at_ms is not None

    async def test_with_no_lane_free_it_waits_while_a_move_is_still_settling(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """Every lane has a team connector, lane 0 two of them, and X moved off
        lane 0 onto lane 1. Once X settles a lane could be free, so the
        fix-up is not done until then."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "keeper-0": (0, None, 100),
            "second-0": (0, None, 5),
            "x-moved": (1, 0, 20),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(1, LANES)}
        await _seed(provider, graph, placements)
        before = {c: (e.lane, e.prev_lane) for c, e in (await _map(provider)).items()}

        first = await _upkeep(provider, graph)

        assert first.migrated_at_ms is None
        assert {c: (e.lane, e.prev_lane) for c, e in (await _map(provider)).items()} == before

        await _settle(provider, "x-moved")
        second = await _upkeep(provider, graph)

        assert _lanes(await _map(provider), "x-moved", "second-0") == (1, 0)
        assert second.migrated_at_ms is not None

    async def test_while_the_connector_that_should_move_is_settling_nobody_takes_the_free_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """One free lane; A (50) has to leave a shared lane but is still
        settling an earlier move; B (10) shares another lane."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "c-200": (3, None, 200),
            "a-50": (3, 4, 50),
            "big-4": (4, None, 300),
            "x-100": (0, None, 100),
            "b-10": (0, None, 10),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in (1, 2, 5, 6)}
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph)

        assert _lanes(await _map(provider), "a-50", "b-10") == (3, 0)
        assert first.migrated_at_ms is None

        await _settle(provider, "a-50")
        second = await _upkeep(provider, graph)

        assert _lanes(await _map(provider), "a-50", "b-10") == (7, 0)
        assert second.migrated_at_ms is None

        await _settle(provider, "a-50")
        third = await _upkeep(provider, graph)

        assert (await _map(provider))["b-10"].lane == 0
        assert third.migrated_at_ms is not None

    async def test_one_that_moved_onto_a_shared_lane_moves_again_once_settled(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """Lane 0: GitLab (100). Slack (40) moved off lane 0 onto lane 1,
        beside Drive (80). Lane 7 is free."""
        placements: dict[str, tuple[int, int | None, int]] = {
            "gitlab-100": (0, None, 100),
            "slack-40": (1, 0, 40),
            "drive-80": (1, None, 80),
        }
        placements |= {f"solo-{lane}": (lane, None, 5) for lane in range(2, 7)}
        await _seed(provider, graph, placements)

        first = await _upkeep(provider, graph)

        assert (await _map(provider))["slack-40"].lane == 1
        assert first.migrated_at_ms is None

        await _settle(provider, "slack-40")
        await _upkeep(provider, graph)
        done = await _upkeep(provider, graph)

        entries = await _map(provider)
        assert _lanes(entries, "slack-40", "drive-80", "gitlab-100") == (7, 1, 0)
        assert done.migrated_at_ms is not None

    async def test_a_lane_an_edition_rule_picks_or_keeps_is_its_decision(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """An edition may put a connector beside another large one, or keep
        it where it is; the fix-up takes either as decided."""
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

        first = await _upkeep(provider, graph, assignments=onto_drive)
        second = await _upkeep(provider, graph, assignments=onto_drive)

        entries = await _map(provider)
        assert _lanes(entries, SLACK, "drive-1") == (drive_lane, drive_lane)
        assert first.migrated_at_ms is None
        assert second.migrated_at_ms is not None


class TestUnknownCountsAreNeverReadAsEmpty:
    async def test_an_unknown_queue_count_keeps_everyone_where_they_are_until_it_is_known(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        graph.fail_stats = {GITLAB}

        first = await _upkeep(provider, graph)

        assert first.migrated_at_ms is None
        assert {e.lane for e in (await _map(provider)).values()} == {SHARED}

        graph.fail_stats = set()
        await _upkeep(provider, graph)

        entries = await _map(provider)
        assert (entries[GITLAB].lane, entries[SLACK].prev_lane) == (SHARED, SHARED)
        assert entries[SLACK].lane != SHARED
        assert (await _upkeep(provider, graph)).migrated_at_ms is not None

    async def test_a_live_connector_the_scan_missed_still_shares_its_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        """A removal in a page already read shifts the next page by one. The
        connector it hides is still on its lane, so that lane is not sealed."""
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
        assignments = _assignments(provider)
        await assignments.record_at_hash_lane(GITLAB, "team")
        await assignments.record_at_hash_lane(SLACK, "team")
        graph.hidden_from_scan = {SLACK}

        first = await _upkeep(provider, graph)

        assert {e.lane for e in (await _map(provider)).values()} == {SHARED}
        assert first.migrated_at_ms is None

        graph.hidden_from_scan = set()
        await _upkeep(provider, graph)

        assert (await _map(provider))[SLACK].lane != SHARED


class TestConnectorsFromBeforeOrgWasOnTheDocument:
    async def test_the_org_comes_from_the_edge_so_the_backlog_keeps_the_lane(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        # The backlog is on the connector whose id sorts last, so a tie of
        # zeros (an unread org) would give the lane to the other one.
        keeper, mover = max(GITLAB, SLACK), min(GITLAB, SLACK)
        graph.add(keeper, queued=40, org_on_document=False)
        graph.add(mover, queued=6, org_on_document=False)

        await _upkeep(provider, graph)

        entries = await _map(provider)
        assert entries[keeper].lane == SHARED
        assert entries[mover].lane != SHARED

    async def test_with_no_org_to_be_found_nobody_moves_and_it_tries_again_later(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40, org_on_document=False, org_edge=False)
        graph.add(SLACK, queued=6, org_on_document=False, org_edge=False)

        report = await _upkeep(provider, graph)

        assert {e.lane for e in (await _map(provider)).values()} == {SHARED}
        assert report.migrated_at_ms is None

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
        graph.add(GITLAB, queued=40)
        graph.add(SLACK, queued=6)
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

    async def test_the_lane_metrics_are_published_by_lane_never_by_connector(
        self, provider: FakeRedisConnectionProvider, graph: _Graph
    ) -> None:
        graph.add(GITLAB, queued=40)
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
