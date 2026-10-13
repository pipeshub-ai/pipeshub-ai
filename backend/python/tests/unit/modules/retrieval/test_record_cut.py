"""`record_cut`: the cut by record, its knobs and the FUSION_RECORD_CUT switch."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import pytest

import app.modules.retrieval.record_cut as record_cut_module
from app.modules.retrieval.record_cut import (
    FUSION_RECORD_CUT_ENV_VAR,
    MAX_FUSION_DEPTH,
    MAX_PER_RECORD,
    RecordCut,
    active_record_cut,
    cut_by_record,
    hit_record_id,
    point_record_id,
    record_cut_from_env,
)
from app.services.vector_db.models import SearchResult

if TYPE_CHECKING:
    from collections.abc import Callable

_KNOBS = (
    "SEARCH_FUSION_DEPTH",
    "SEARCH_NEW_RECORD_SHARE",
    "SEARCH_PER_RECORD_FLOOR",
    "SEARCH_PER_RECORD_DIVISOR",
)


def _point(index: int, vrid: str | None) -> SearchResult:
    metadata = {} if vrid is None else {"virtualRecordId": vrid}
    return SearchResult(id=f"p{index}", score=1.0 - index / 1000, payload={"metadata": metadata})


def _ranked(*vrids: str | None) -> list[SearchResult]:
    return [_point(i, vrid) for i, vrid in enumerate(vrids)]


def _cut(results: list[SearchResult], limit: int, *, per_record: int = 2, reserve: int = 0) -> list[str]:
    kept = cut_by_record(results, limit, per_record=per_record, reserve=reserve, record_of=point_record_id)
    return [point.id for point in kept]


class TestCutByRecord:
    def test_the_reserve_reaches_a_record_just_past_a_plain_cut(self) -> None:
        """The strong single-leg hit: a new record's best point past ``limit``
        displaces a long record's lower-ranked extra points."""
        results = _ranked("a", "b", "a", "a", "b", "c")
        assert _cut(results, 4, per_record=10) == ["p0", "p1", "p2", "p3"]
        assert _cut(results, 4, per_record=10, reserve=2) == ["p0", "p1", "p2", "p5"]

    def test_one_record_cannot_fill_the_head(self) -> None:
        results = _ranked("a", "a", "a", "a", "b", "c")
        assert _cut(results, 4, per_record=2) == ["p0", "p1", "p4", "p5"]

    def test_a_search_confined_to_one_record_still_returns_limit_in_rank_order(self) -> None:
        results = _ranked("a", "a", "a", "a", "a")
        assert _cut(results, 3, per_record=1, reserve=2) == ["p0", "p1", "p2"]

    def test_a_short_list_passes_through(self) -> None:
        assert _cut(_ranked("a", "a"), 5, per_record=1) == ["p0", "p1"]

    def test_results_without_a_record_are_not_capped_together(self) -> None:
        assert _cut(_ranked(None, None, None, "a"), 3, per_record=1) == ["p0", "p1", "p2"]

    def test_the_kept_results_stay_in_rank_order(self) -> None:
        assert _cut(_ranked("a", "a", "a", "b"), 2, per_record=1, reserve=1) == ["p0", "p3"]

    @pytest.mark.parametrize("limit", [0, -3])
    def test_a_limit_below_one_keeps_everything(self, limit: int) -> None:
        """As `merge_collection_results` reads it: no cap, not an empty cut."""
        assert _cut(_ranked("a", "a", "b"), limit) == ["p0", "p1", "p2"]

    def test_a_negative_reserve_never_returns_more_than_limit(self) -> None:
        assert _cut(_ranked("a", "b", "c", "d", "e"), 2, per_record=1, reserve=-5) == ["p0", "p1"]

    def test_a_reserve_past_limit_gives_every_slot_to_new_records(self) -> None:
        assert _cut(_ranked("a", "a", "b", "b", "c"), 3, per_record=5, reserve=9) == ["p0", "p2", "p4"]

    def test_a_per_record_cap_below_one_still_takes_each_records_best_point(self) -> None:
        assert _cut(_ranked("a", "a", "b", "c"), 3, per_record=0) == ["p0", "p2", "p3"]

    def test_invariants_over_random_rankings(self) -> None:
        """Whatever the ranking: exactly min(limit, n) kept, in rank order, the
        best hit always kept, and never fewer records than a plain cut shows."""
        rng = random.Random(0)
        for _ in range(2000):
            n = rng.randint(0, 40)
            records = [rng.choice("abcdefgh") if rng.random() > 0.1 else None for _ in range(n)]
            results = _ranked(*records)
            limit = rng.randint(1, 20)
            per_record = rng.randint(0, 6)
            reserve = rng.randint(-2, limit + 2)

            kept = cut_by_record(
                results, limit, per_record=per_record, reserve=reserve, record_of=point_record_id
            )

            positions = [results.index(point) for point in kept]
            assert len(kept) == min(limit, n)
            assert positions == sorted(positions)
            if n:
                assert positions[0] == 0

            def records_in(points: list[SearchResult]) -> int:
                return len({point_record_id(p) or p.id for p in points})

            assert records_in(kept) >= records_in(results[:limit])


class TestLongDocuments:
    """One record with many hits: what it keeps, and when it keeps everything."""

    @staticmethod
    def _long_then_others(long_hits: int, others: int) -> list[SearchResult]:
        return _ranked(*(["long"] * long_hits), *(f"r{i}" for i in range(others)))

    def test_a_long_record_keeps_its_best_points_and_the_rest_reach_other_records(self) -> None:
        results = self._long_then_others(long_hits=40, others=30)
        cut = RecordCut()

        kept = cut.cut(results, 20, point_record_id)

        long_kept = [p.id for p in kept if point_record_id(p) == "long"]
        assert long_kept == ["p0", "p1", "p2", "p3"]
        assert cut.per_record(20) == len(long_kept)
        assert len({point_record_id(p) for p in kept}) == 17

    def test_a_long_record_alone_within_the_fused_depth_keeps_every_slot(self) -> None:
        """Nothing else to spread to: the fill gives the slots back in rank order."""
        results = self._long_then_others(long_hits=60, others=0)

        kept = RecordCut().cut(results, 20, point_record_id)

        assert [p.id for p in kept] == [f"p{i}" for i in range(20)]

    def test_a_larger_per_record_floor_keeps_more_of_a_long_record(self) -> None:
        results = self._long_then_others(long_hits=40, others=30)

        kept = RecordCut(per_record_floor=12).cut(results, 20, point_record_id)

        assert sum(point_record_id(p) == "long" for p in kept) == 10

    def test_at_the_default_search_limit_one_record_holds_at_most_eight_of_the_head(self) -> None:
        results = self._long_then_others(long_hits=150, others=100)

        kept = RecordCut().cut(results, 50, point_record_id)

        assert RecordCut().per_record(50) == 8
        assert sum(point_record_id(p) == "long" for p in kept) == 8
        assert len(kept) == 50


class TestRecordCutSettings:
    @pytest.mark.parametrize(
        ("limit", "expected"),
        [(4, 12), (20, 60), (50, 150), (100, 300), (200, 300), (300, 300), (400, 400), (0, 0), (-1, -1)],
    )
    def test_fusion_is_three_times_deeper_within_the_cap_and_never_below_the_limit(
        self, limit: int, expected: int
    ) -> None:
        assert RecordCut().fused_limit(limit, 300) == expected

    def test_no_depth_or_limit_asks_past_the_cap_or_below_the_limit(self) -> None:
        for depth in range(1, MAX_FUSION_DEPTH + 1):
            for limit in range(1, 501):
                fused = RecordCut(depth=depth).fused_limit(limit, 300)
                assert limit <= fused <= max(limit, 300)

    def test_the_deepest_fusion_still_stops_at_the_cap(self) -> None:
        assert RecordCut(depth=MAX_FUSION_DEPTH).fused_limit(50, 300) == 300

    def test_depth_one_asks_for_the_limit_alone(self) -> None:
        assert RecordCut(depth=1).fused_limit(50, 300) == 50

    @pytest.mark.parametrize(("limit", "expected"), [(5, 4), (20, 4), (50, 8), (60, 10), (300, 50)])
    def test_per_record_cap(self, limit: int, expected: int) -> None:
        assert RecordCut().per_record(limit) == expected


@pytest.fixture
def knobs(monkeypatch: pytest.MonkeyPatch) -> Callable[..., RecordCut]:
    def _set(**env: str) -> RecordCut:
        for name in _KNOBS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return record_cut_from_env()

    return _set


class TestKnobsFromEnv:
    def test_defaults(self, knobs) -> None:
        assert knobs() == RecordCut(depth=3, new_record_share=0.5, per_record_floor=4, per_record_divisor=6)

    def test_blank_values_keep_the_defaults(self, knobs) -> None:
        """Compose passes an unset `${NAME:-}` as an empty string."""
        assert knobs(**dict.fromkeys(_KNOBS, "")) == RecordCut()

    def test_overrides(self, knobs) -> None:
        cut = knobs(
            SEARCH_FUSION_DEPTH="2",
            SEARCH_NEW_RECORD_SHARE="0.25",
            SEARCH_PER_RECORD_FLOOR="8",
            SEARCH_PER_RECORD_DIVISOR="3",
        )
        assert cut == RecordCut(depth=2, new_record_share=0.25, per_record_floor=8, per_record_divisor=3)
        assert cut.per_record(60) == 20

    def test_malformed_values_keep_the_defaults(self, knobs) -> None:
        assert knobs(
            SEARCH_FUSION_DEPTH="deep",
            SEARCH_NEW_RECORD_SHARE="nan",
            SEARCH_PER_RECORD_FLOOR="4.5",
            SEARCH_PER_RECORD_DIVISOR="six",
        ) == RecordCut()

    @pytest.mark.parametrize(
        ("env", "field", "expected"),
        [
            ({"SEARCH_FUSION_DEPTH": "0"}, "depth", 1),
            ({"SEARCH_FUSION_DEPTH": "-4"}, "depth", 1),
            ({"SEARCH_FUSION_DEPTH": "50"}, "depth", MAX_FUSION_DEPTH),
            ({"SEARCH_NEW_RECORD_SHARE": "1.7"}, "new_record_share", 1.0),
            ({"SEARCH_NEW_RECORD_SHARE": "-0.2"}, "new_record_share", 0.0),
            ({"SEARCH_NEW_RECORD_SHARE": "inf"}, "new_record_share", 0.5),
            ({"SEARCH_PER_RECORD_FLOOR": "0"}, "per_record_floor", 1),
            ({"SEARCH_PER_RECORD_FLOOR": "100000"}, "per_record_floor", MAX_PER_RECORD),
            ({"SEARCH_PER_RECORD_DIVISOR": "0"}, "per_record_divisor", 1),
            ({"SEARCH_PER_RECORD_DIVISOR": "100000"}, "per_record_divisor", MAX_PER_RECORD),
        ],
    )
    def test_out_of_range_values_are_clamped(self, knobs, env: dict[str, str], field: str, expected: object) -> None:
        assert getattr(knobs(**env), field) == expected


def _switches(monkeypatch: pytest.MonkeyPatch, mode: str | None, feature: str | None) -> None:
    for name, value in (("HIGH_ACCURACY_MODE", mode), (FUSION_RECORD_CUT_ENV_VAR, feature)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


class TestSwitch:
    @pytest.mark.parametrize(
        ("mode", "feature"),
        [(None, None), ("false", None), ("false", ""), ("true", "false"), ("true", "0"), (None, "1"), (None, "yes")],
    )
    def test_off(self, monkeypatch: pytest.MonkeyPatch, mode: str | None, feature: str | None) -> None:
        _switches(monkeypatch, mode, feature)
        assert active_record_cut() is None

    @pytest.mark.parametrize(
        ("mode", "feature"),
        [("true", None), ("true", ""), ("true", "   "), (None, "true"), ("false", "true"), ("false", " TRUE ")],
    )
    def test_on(self, monkeypatch: pytest.MonkeyPatch, mode: str | None, feature: str | None) -> None:
        _switches(monkeypatch, mode, feature)
        assert active_record_cut() == record_cut_module._SETTINGS

    def test_read_on_every_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _switches(monkeypatch, "true", None)
        assert active_record_cut() is not None
        monkeypatch.setenv(FUSION_RECORD_CUT_ENV_VAR, "false")
        assert active_record_cut() is None

    def test_the_state_is_logged_once_and_again_when_it_changes(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(record_cut_module, "_logged", record_cut_module._LoggedState())
        _switches(monkeypatch, None, None)
        with caplog.at_level(logging.INFO, logger=record_cut_module.__name__):
            active_record_cut()
            active_record_cut()
            monkeypatch.setenv(FUSION_RECORD_CUT_ENV_VAR, "true")
            active_record_cut()
            active_record_cut()

        messages = [r.getMessage() for r in caplog.records if r.name == record_cut_module.__name__]
        assert len(messages) == 2
        assert messages[0].startswith("Search record cut off")
        assert messages[1].startswith("Search record cut on")


class TestRecordOf:
    def test_a_point_without_a_payload_has_no_record(self) -> None:
        assert point_record_id(SearchResult(id="x", score=1.0, payload=None)) is None
        assert point_record_id(SearchResult(id="x", score=1.0, payload={"metadata": None})) is None

    def test_a_hit_without_metadata_has_no_record(self) -> None:
        assert hit_record_id({"metadata": None}) is None
        assert hit_record_id({"metadata": {"virtualRecordId": "v1"}}) == "v1"
