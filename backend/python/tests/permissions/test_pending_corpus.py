"""The corpus leak table: a structural change is half done (old stamps in the index, new scopes for the viewer) and the filter must admit
nothing of the changed subtree and nothing the new world denies. Run on the filter tree and on the pgvector rule written out in Python;
`tests/integration/vector_db/test_pgvector_pending_corpus.py` runs the same table against real pgvector."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.vector_db import scope_expr as sx
from tests.unit.services.vector_db.test_scope_filter_stores import pg_rule

from .scope_corpus import SCENARIOS, build, leaks

BREAKS = {
    "pending_dropped": lambda f: replace(f, pending=frozenset()),
    "pending_as_excluded": lambda f: replace(f, pending=frozenset(), excluded=f.excluded | f.pending),
    "blocked_dropped": lambda f: replace(f, blocked=frozenset()),
}
RULES = {"tree": lambda f, p: sx.evaluate(sx.admits(f), p), "pgvector rule": lambda f, p: pg_rule(f, p)}


async def leak_table(rule, *, broken: str | None = None) -> dict[str, list]:
    table = {}
    for scenario in SCENARIOS:
        corpus = await build(scenario)
        admitted = {}
        for viewer, (scopes, _) in corpus.viewers.items():
            flt = corpus.scope_filter(scopes)
            flt = BREAKS[broken](flt) if broken else flt
            admitted[viewer] = {rid for rid, payload in corpus.points.items() if rule(flt, payload)}
        found = leaks(corpus, admitted)
        if found:
            table[scenario.id] = found
    return table


def test_the_corpus_holds_the_structural_scenarios() -> None:
    assert len(SCENARIOS) >= 16, [s.id for s in SCENARIOS]


@pytest.mark.parametrize("rule", sorted(RULES))
async def test_a_half_done_change_admits_nothing_of_its_subtree_and_nothing_the_new_world_denies(rule: str) -> None:
    assert await leak_table(RULES[rule]) == {}


@pytest.mark.parametrize("broken", sorted(BREAKS))
@pytest.mark.parametrize("rule", sorted(RULES))
async def test_the_table_notices_a_filter_that_does_not_hide_the_pending_subtree(rule: str, broken: str) -> None:
    table = await leak_table(RULES[rule], broken=broken)
    print(f"\n{rule} / {broken}: {len(table)} of {len(SCENARIOS)} scenarios leak: {sorted(table)}")
    assert table
