"""Trackers nest: an effect is recorded in every active tracker, each by its own collection filter."""

from __future__ import annotations

from app.services.graph_db import edge_effects


def test_an_outer_tracker_sees_the_edges_created_inside_an_inner_one() -> None:
    with edge_effects.track() as outer:
        with edge_effects.track({"belongsTo"}) as inner:
            edge_effects.note_created(["a", "b"], "belongsTo")
            edge_effects.note_created(["c"], "permission")
        assert inner == {"a", "b"}
        edge_effects.note_created(["d"], "permission")
    assert outer == {"a", "b", "c", "d"}
    assert not edge_effects.tracking()


def test_each_tracker_applies_its_own_filter() -> None:
    with edge_effects.track({"permission"}) as outer, edge_effects.track({"belongsTo"}) as inner:
        edge_effects.note_created(iter(["x"]), "belongsTo")  # an iterator is read once for all of them
    assert inner == {"x"} and outer == set()


def test_no_tracker_records_nothing() -> None:
    edge_effects.note_created(["x"], "belongsTo")
    assert not edge_effects.tracking()
