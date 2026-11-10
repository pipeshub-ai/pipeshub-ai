"""Unit tests (no store): the corpus against the product oracle."""

from __future__ import annotations

import pytest

from . import deltas
from .runner import before_misses, scenario_misses
from .scenario import Scenario, load_all

SCENARIOS = load_all()
IDS = [s.id for s in SCENARIOS]


def test_ids_are_unique() -> None:
    assert len(IDS) == len(set(IDS))


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_before_verdicts_match_oracle(scenario: Scenario) -> None:
    assert before_misses(scenario) == []


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if not s.expect.source_delta], ids=lambda s: s.id)
def test_listed_verdicts_match_oracle(scenario: Scenario) -> None:
    assert scenario_misses(scenario) == []


def test_every_difference_is_a_listed_delta() -> None:
    assert deltas.check(SCENARIOS, deltas.load_deltas()) == []


def _has_children(s: Scenario, node: str) -> bool:
    return any(p.id == node for n in s.world.nodes for p in n.parents)


def test_changes_to_where_a_node_sits_rewrite_the_index() -> None:
    """Moving a node or breaking its inheritance rewrites every descendant; a leaf is one write."""
    for s in SCENARIOS:
        for m in s.mutations:
            where = m.kind == "moved" or (m.kind == "inherit_set" and _has_children(s, m.node))
            if where and s.expect.index_writes is not None:
                want = "subtree" if _has_children(s, m.node) else "1"
                assert s.expect.index_writes == want, f"{s.id}: {m.kind} on {m.node} writes {want}"
