"""accessLimit ORG opens a connector to the members of its own org only: a user who belongs only to org A is denied an ORG app of org B,
even when the check is asked under org B. The conformance runner always asks under the world's org (RULE-L07 covers that); this asks under the
app's. The store test needs the throwaway stores of `tests/integration/graph_permissions/docker-compose.yml`."""

from __future__ import annotations

import pytest

from . import oracle
from .graph import Namespace, compile_world
from .model import Node, Parent, WorldBuilder

ASKED = ["A", "d", "f"]


def _world():
    b = WorldBuilder()
    b.principal("u")                                         # a member of the world's org (org A) only
    b.node(Node("A", "app", org="b", model="APP_LEVEL", access_limit="ORG", created_by="u"))
    b.node(Node("d", "group", connector="A", rule="OPEN", org="b", parents=(Parent("A"),), belongs=("A",)))
    b.node(Node("f", "record", connector="A", rule="OPEN", org="b", parents=(Parent("d"),)))
    b.gate("u", "A")                                         # a link counts for nothing on a limited app
    return b.build()


WORLD = _world()


def test_the_oracle_denies_an_org_app_of_another_org_asked_under_that_org() -> None:
    ns = Namespace("o")
    found = oracle.admitted(compile_world(WORLD, "o"), ns.id("u"), f"{ns.org}-b", [ns.id(n) for n in ASKED])
    assert {n for n, v in found.items() if v.admitted} == set()


@pytest.mark.integration
@pytest.mark.timeout(900)
async def test_each_store_denies_an_org_app_of_another_org_asked_under_that_org(graph_layers) -> None:
    for layer in graph_layers:
        await layer.preload([WORLD])
        ns = await layer._ns(WORLD)
        found = await layer._provider.check_access(ns.id("u"), f"{ns.org}-b", node_ids=[ns.id(n) for n in ASKED])
        assert set(found.node_ids) == set(), layer.name
