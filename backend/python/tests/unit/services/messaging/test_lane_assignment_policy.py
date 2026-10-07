"""The OSS lane rule, as a table of cases.

The rule is a pure function of a connector and a snapshot of every lane's
load, so each case builds the snapshot by hand and asks it directly. The
store's own tests (``test_lane_assignment.py``) run the same rule through the
Redis scripts.
"""
from __future__ import annotations

import pytest

from app.services.messaging.lanes.assignment_policy import (
    KEEP_CURRENT,
    ConnectorClass,
    LaneLoad,
    LaneRequest,
    LaneSnapshot,
    admin_lane_move_allowed,
    choose_connector_lane,
    is_large_class,
)

LANES = 8


def _snapshot(**loads: tuple[int, int] | tuple[int, int, bool]) -> LaneSnapshot:
    """``lane3=(large, small)`` or ``lane3=(large, small, busy)``; unnamed lanes are empty."""
    lanes = []
    for lane in range(LANES):
        large, small, *busy = loads.get(f"lane{lane}", (0, 0))
        lanes.append(LaneLoad(lane=lane, large=large, small=small, busy=bool(busy and busy[0])))
    return LaneSnapshot(lane_count=LANES, lanes=tuple(lanes))


def _request(hash_lane: int, connector_class: str = ConnectorClass.TEAM.value) -> LaneRequest:
    return LaneRequest(connector_id="c", connector_class=connector_class, hash_lane=hash_lane)


def _place_all(classes: list[str], hash_lanes: list[int]) -> list[int]:
    """Place connectors one after another, as the store would."""
    large = [0] * LANES
    small = [0] * LANES
    placed = []
    for connector_class, hash_lane in zip(classes, hash_lanes, strict=True):
        snapshot = LaneSnapshot(
            lane_count=LANES,
            lanes=tuple(LaneLoad(lane, large[lane], small[lane]) for lane in range(LANES)),
        )
        lane = choose_connector_lane(_request(hash_lane, connector_class), snapshot)
        assert isinstance(lane, int)
        placed.append(lane)
        if is_large_class(connector_class):
            large[lane] += 1
        else:
            small[lane] += 1
    return placed


class TestLargeConnectorsNeverShareWhileALaneIsFree:
    def test_eight_team_connectors_that_all_hash_to_one_lane_get_eight_lanes(self) -> None:
        placed = _place_all([ConnectorClass.TEAM.value] * 8, [3] * 8)

        assert sorted(placed) == list(range(LANES))
        assert placed[0] == 3, "the first keeps its hash lane"

    def test_the_ninth_goes_to_a_lane_that_is_not_busy(self) -> None:
        snapshot = _snapshot(**{f"lane{lane}": (1, 0, lane != 6) for lane in range(LANES)})

        assert choose_connector_lane(_request(hash_lane=2), snapshot) == 6

    def test_among_idle_lanes_with_a_large_connector_the_one_with_fewer_small_ones_wins(
        self,
    ) -> None:
        snapshot = _snapshot(**{f"lane{lane}": (1, 1 if lane == 4 else 5) for lane in range(LANES)})

        assert choose_connector_lane(_request(hash_lane=0), snapshot) == 4


class TestSmallConnectorsStayOffBusyLanes:
    def test_a_knowledge_base_avoids_lanes_with_a_large_connector(self) -> None:
        snapshot = _snapshot(lane0=(1, 0), lane1=(1, 0), lane2=(1, 0))

        lane = choose_connector_lane(_request(1, ConnectorClass.KB.value), snapshot)

        assert lane not in (0, 1, 2)

    def test_three_team_connectors_and_seventy_small_ones_never_share_with_a_team_one(
        self,
    ) -> None:
        classes = [ConnectorClass.TEAM.value] * 3 + [ConnectorClass.PERSONAL.value] * 70
        hash_lanes = [i % LANES for i in range(len(classes))]

        placed = _place_all(classes, hash_lanes)

        team_lanes = set(placed[:3])
        small_lanes = placed[3:]
        assert len(team_lanes) == 3
        assert not team_lanes & set(small_lanes)
        counts = [small_lanes.count(lane) for lane in sorted(set(small_lanes))]
        assert max(counts) - min(counts) <= 1, f"spread evenly, got {counts}"


class TestTheHashLaneIsKeptWhenItTies:
    @pytest.mark.parametrize("hash_lane", range(LANES))
    def test_on_an_empty_install_every_connector_keeps_its_hash_lane(self, hash_lane: int) -> None:
        assert choose_connector_lane(_request(hash_lane), _snapshot()) == hash_lane

    def test_connectors_that_never_collided_see_no_change(self) -> None:
        assert _place_all([ConnectorClass.TEAM.value] * 4, [5, 1, 7, 2]) == [5, 1, 7, 2]

    def test_a_taken_hash_lane_is_not_kept(self) -> None:
        assert choose_connector_lane(_request(3), _snapshot(lane3=(1, 0))) == 0

    def test_a_busy_hash_lane_does_not_tie_with_an_idle_one(self) -> None:
        assert choose_connector_lane(_request(3), _snapshot(lane3=(0, 0, True))) == 0

    def test_a_hash_lane_outside_the_snapshot_is_ignored(self) -> None:
        assert choose_connector_lane(_request(12), _snapshot()) == 0


class TestDeterminism:
    def test_ties_go_to_the_lowest_lane(self) -> None:
        snapshot = _snapshot(lane0=(1, 0), lane1=(1, 0))

        assert choose_connector_lane(_request(0), snapshot) == 2

    def test_no_lanes_at_all_falls_back_to_the_hash_lane(self) -> None:
        empty = LaneSnapshot(lane_count=0, lanes=())

        assert choose_connector_lane(_request(4), empty) == 4

    def test_the_oss_rule_never_keeps_a_lane_by_saying_so(self) -> None:
        assert choose_connector_lane(_request(4), _snapshot()) is not KEEP_CURRENT


def test_only_team_connectors_count_as_large() -> None:
    assert is_large_class("team")
    assert not any(is_large_class(c) for c in ("personal", "kb", "system", "pinned"))


def test_this_edition_never_lets_an_administrator_move_a_connector() -> None:
    assert admin_lane_move_allowed("org-1", "slack-1") is False
