"""Seed resolution: which nodes may start a grants pass.

These are **spec tests, not regression tests for the write path**: the query
they exercise is ``seed_query.py``, which is test Cypher, run against the
hand-built fixture. They pin the agreed seed rule and prove the two backends
agree on it, so the providers' implementation has something to be judged
against — no product code changes when they fail.

The seed rule is the narrow one: granted, non-strict, not deleted, inside a
partition the gate admits. Strictness is the load-bearing exclusion — a strict
node needs every ancestor, so one below a gap can never qualify and seeding it
over-shares.
"""

import pytest
from neo4j import AsyncGraphDatabase

from .seed_query import gated_app_ids, seeds_by_app
from .test_qpp_semantics import GRANTEES

pytestmark = pytest.mark.integration

USER = "user-u"
ORG = "org-1"


async def _with_session(settings: dict, fn):
    driver = AsyncGraphDatabase.driver(
        settings["uri"], auth=(settings["username"], settings["password"])
    )
    try:
        async with driver.session(database=settings["database"]) as session:
            return await fn(session)
    finally:
        await driver.close()


async def test_the_gate_admits_only_apps_the_user_can_reach(
    loaded_graph, neo4j_settings
) -> None:
    """The gate: a user-app relation, or any permission that reaches the user.

    gate-app has neither, and kb-2 is a collection nobody granted — an
    explicit grant is the only way into a collection.
    """
    apps = await _with_session(
        neo4j_settings, lambda s: gated_app_ids(s, USER, ORG, GRANTEES)
    )

    assert apps == {
        "ex1-app", "ex2-app", "dec-app", "dec-rgl-app", "ex-app",
        "swm-app", "pl-app", "gp-app", "flag-app", "kb-1", "deep-app",
    }, sorted(apps)
    assert "gate-app" not in apps, "an unreachable app must not be gated in"
    assert "kb-2" not in apps, "a collection with no grant stays out"


async def test_every_grant_path_produces_a_seed(loaded_graph, neo4j_settings) -> None:
    """All five grant paths: user, group, role, team and org.

    On the ORG path the org node is just another grantee, so gp-org seeds
    only because orgnode-1 is in the grantee list — drop it and this fails.
    The TEAM path is resolved in preparation rather than matched inline.
    """
    seeds = await _with_session(
        neo4j_settings, lambda s: seeds_by_app(s, USER, ORG, GRANTEES)
    )
    gp = set(seeds.get("gp-app", []))

    assert gp == {"gp-user", "gp-group", "gp-role", "gp-team", "gp-org"}, sorted(gp)
    assert "gp-other-org" not in gp, "a grant held by another org must never seed"


async def test_a_gated_app_produces_no_seeds(loaded_graph, neo4j_settings) -> None:
    """The gate is computed first, so a grant inside an app the user
    cannot reach produces no seed at all — pruning it later would be too late,
    because the seed itself is an entry point."""
    seeds = await _with_session(
        neo4j_settings, lambda s: seeds_by_app(s, USER, ORG, GRANTEES)
    )
    every_seed = {s for group in seeds.values() for s in group}

    assert "gate-app" not in seeds, sorted(seeds)
    assert "gate-rg1" not in every_seed, "a granted group behind the gate must not seed"
    assert "gate-r3" not in every_seed, "nor a granted record behind it"


async def test_strict_nodes_are_never_seeded(loaded_graph, neo4j_settings) -> None:
    """Example 1's spaces are both strict and both granted to U,
    so they are exactly the shape that must be refused, and flag-granted is the
    record-level equivalent."""
    seeds = await _with_session(
        neo4j_settings, lambda s: seeds_by_app(s, USER, ORG, GRANTEES)
    )
    every_seed = {s for group in seeds.values() for s in group}

    assert "ex1-app" not in seeds, "both Example 1 spaces are strict; neither may seed"
    assert "ex1-rg1" not in every_seed and "ex1-rg2" not in every_seed
    assert "flag-granted" not in every_seed, "a strict record must not seed either"

    # Guards the guard: the same app's non-strict grants do seed, so the
    # assertions above cannot pass merely because seeding returned nothing.
    assert set(seeds.get("ex2-app", [])) == {"ex2-r4", "ex2-r6"}, sorted(
        seeds.get("ex2-app", [])
    )


async def test_seeds_below_a_gap_are_found(loaded_graph, neo4j_settings) -> None:
    """The placement cases are reachable only as seeds: each sits under an
    inaccessible ancestor, which is the whole reason the grants pass exists."""
    seeds = await _with_session(
        neo4j_settings, lambda s: seeds_by_app(s, USER, ORG, GRANTEES)
    )

    assert set(seeds.get("pl-app", [])) == {
        "pl-r6", "pl-r9", "pl-r11", "pl-rg3", "pl-r12", "pl-rg4", "pl-r14", "pl-r15",
    }, sorted(
        seeds.get("pl-app", [])
    )
