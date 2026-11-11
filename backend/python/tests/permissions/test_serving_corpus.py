"""The deferring scope filter against the oracle, on the whole scenario corpus: every connector served, the index holding what the projector
stamps, each viewer's scopes from the oracle. A point the stamps decide must agree with the oracle both ways (no over-admit, no recall loss);
a record that belongs to no record group is left to the request's arms (its stamp is `legacy`), because the connector arm returns it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.permissions.scope_fields import set_resolved_generation
from app.services.vector_db import scope_expr as sx

from .serving_corpus import SCENARIOS, ServingCorpus, build, judge

if TYPE_CHECKING:
    from collections.abc import Iterator

GENERATION = 3


@pytest.fixture(autouse=True)
def _generation() -> Iterator[None]:
    set_resolved_generation(GENERATION)
    yield
    set_resolved_generation(None)


def returned(corpus: ServingCorpus, **kw: object) -> dict[str, set[str]]:
    """The records each viewer's search returns through the tree: the request's own arms are not in it, so a deferred point is returned."""
    out = {}
    for viewer, (scopes, _) in corpus.viewers.items():
        node = sx.admits(corpus.scope_filter(scopes, **kw))  # type: ignore[arg-type]
        out[viewer] = {rid for rid, payload in corpus.points.items() if sx.evaluate(node, payload)}
    return out


async def test_no_over_admit_and_no_recall_loss_on_the_corpus() -> None:
    over, lost, unreached, pairs = [], [], [], 0
    for scenario in SCENARIOS:
        corpus = await build(scenario)
        verdict = judge(corpus, returned(corpus))
        pairs += len(corpus.points) * len(corpus.viewers)
        over += [(scenario.id, *p) for p in verdict.over]
        lost += [(scenario.id, *p) for p in verdict.lost]
        unreached += [(scenario.id, *p) for p in verdict.unreached]
    assert pairs > 3000
    assert over == [] and lost == []
    assert unreached == []      # the connector arm returns every record of a gated app, so a hidden record the oracle admits is a loss, not a miss


async def test_a_connector_that_is_not_served_is_left_to_the_request_whole() -> None:
    """The other connectors keep their stamps' verdicts; a point of this one is returned by the tree whatever its stamp says, and the arms and the
    post-check decide it."""
    checked = 0
    for scenario in SCENARIOS:
        corpus = await build(scenario)
        if len(corpus.connectors) < 2:
            continue
        first, *rest = sorted(corpus.connectors)
        served = frozenset(rest)
        got = returned(corpus, served=served)
        verdict = judge(corpus, got, served)
        assert verdict.over == [] and verdict.lost == [], scenario.id
        for viewer in corpus.viewers:
            assert {r for r, p in corpus.points.items() if p["connectorIds"] == [first]} <= got[viewer]
        checked += 1
    assert checked


async def test_nothing_served_leaves_every_point_to_the_request() -> None:
    for scenario in SCENARIOS[:20]:
        corpus = await build(scenario)
        assert all(r == set(corpus.points) for r in returned(corpus, served=frozenset()).values())


async def test_the_judge_sees_an_over_admit_and_a_recall_loss() -> None:
    """The controls: a viewer who reads everything admits what the oracle denies, and one who reads nothing hides what it admits."""
    from dataclasses import replace
    over = lost = 0
    for scenario in SCENARIOS:
        corpus = await build(scenario)
        groups = frozenset(g for _, (sc, _) in corpus.viewers.items() for g in sc.readable_groups)
        everything = {v: replace(sc, readable_groups=frozenset(n for n in groups | sc.readable_groups), open_apps=corpus.connectors, readable_scopes=frozenset(
            i for p in corpus.points for i in [p])) for v, (sc, _) in corpus.viewers.items()}
        nothing = {v: replace(sc, readable_groups=frozenset(), open_apps=frozenset(), readable_scopes=frozenset()) for v, (sc, _) in corpus.viewers.items()}
        for scopes_by_viewer, kind in ((everything, "over"), (nothing, "lost")):
            got = {v: {r for r, p in corpus.points.items() if sx.evaluate(sx.admits(corpus.scope_filter(sc)), p)} for v, sc in scopes_by_viewer.items()}
            verdict = judge(corpus, got)
            over += kind == "over" and len(verdict.over)
            lost += kind == "lost" and len(verdict.lost)
    assert over > 20 and lost > 20, (over, lost)
