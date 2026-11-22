"""A connector's access limit flips null -> CREATOR -> ORG -> null -> CREATOR in place, with the search access-list cache warm
and never dropped (the worst case: an invalidation lost or still settling), on Neo4j and ArangoDB. After each flip, with no
wait: the access check equals the oracle on every viewer x node pair, the search access list names no record of a limited connector the oracle
denies, and entity search and the team list reach the limited connector exactly as its limit says. A tightening is seen at
once because the gate is read live; the cache keeps only what a connector holds, never who passes its gate.
(PostgreSQL: ``graph_db/postgres/test_access_limit_flip_pg.py``.) Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import aiohttp
import pytest
from neo4j import AsyncGraphDatabase

from app.services.cache.accessible_records_cache import AccessibleRecordsCache
from tests.unit.services.cache.test_accessible_records_cache import FakeRedis

from . import oracle
from .graph import compile_world
from .limit_reach import reach_findings
from .scenario import load_all

if TYPE_CHECKING:
    from .model import World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]

# (accessLimit, permissionModel) as the processor writes them: a limit is written with APP_LEVEL, a cleared limit restores
# the connector type's model (here record-level, no model).
STEPS = [(None, None), ("CREATOR", "APP_LEVEL"), ("ORG", "APP_LEVEL"), (None, None), ("CREATOR", "APP_LEVEL")]


def flip_world() -> World:
    """RULE-L11: one connector A every link kind reaches, from inside and outside the org, and a connector B never limited."""
    return next(s.final_world() for s in load_all() if s.id == "RULE-L11")


def at_step(world: World, limit: str | None, model: str | None) -> World:
    return world.with_node(replace(world.node("A"), access_limit=limit, model=model))


async def _set_neo4j(settings: dict, app: str, limit: str | None, model: str | None) -> None:
    driver = AsyncGraphDatabase.driver(settings["uri"], auth=(settings["username"], settings["password"]))
    try:
        async with driver.session(database=settings["database"]) as session:
            await session.run("MATCH (a:App {id: $id}) SET a.accessLimit = $limit, a.permissionModel = $model", id=app, limit=limit, model=model)
    finally:
        await driver.close()


async def _set_arango(settings: dict, app: str, limit: str | None, model: str | None) -> None:
    base = settings["url"].rstrip("/")
    async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(settings["username"], settings["password"])) as session:
        # keepNull: a cleared limit is stored null, as the product stores it on ArangoDB
        async with session.patch(f"{base}/_db/{settings['db']}/_api/document/apps/{app}?keepNull=true",
                                 json={"accessLimit": limit, "permissionModel": model}) as resp:
            assert resp.status in (200, 201, 202), await resp.text()


async def step_findings(provider, world, graph, ns, team=None) -> list[str]:
    """The access check, the search access list, entity search and the team list of every viewer against the oracle."""
    found: list[str] = []
    asked = [n.id for n in world.nodes]
    store = oracle.Store(graph)
    limited = {n["id"] for n in graph["nodes"] if n["kind"] == "App" and n["props"].get("accessLimit") is not None}
    limited_app = {n["id"]: n["props"].get("connectorId") in limited for n in graph["nodes"] if n["kind"] == "Record"}
    for viewer in world.users():
        user = ns.id(viewer)
        who = oracle.viewer(store, user, ns.org)
        want = {n for n in asked if oracle.verdict(store, who, ns.id(n)).admitted}
        check = await provider.check_access(user, ns.org, node_ids=[ns.id(n) for n in asked])
        got = {ns.local(n) for n in check.node_ids - check.placeholder_ids}
        found += [f"check {viewer}: over-admits {sorted(got - want)}"] if got - want else []
        found += [f"check {viewer}: under-admits {sorted(want - got)}"] if want - got else []
        listed = await provider.get_accessible_virtual_record_ids(user, ns.org, raise_on_error=True)
        # Only a limited connector's records: on an unlimited one the list is a pre-filter that every search hit is
        # re-checked after (retrieval_service._keep_permitted_hits), and it is allowed to be wider there.
        leaked = sorted(ns.local(r) for r in listed.values() if limited_app.get(r) and ns.local(r) not in want)
        found += [f"search {viewer}: lists records it may not read {leaked}"] if leaked else []
    found += await reach_findings(world, graph, ns, lambda u: provider.get_entity_access_context(u, ns.org), team)
    return found


async def test_flipping_the_limit_is_seen_at_once_with_the_search_cache_warm(graph_layers, neo4j_settings, arango_settings) -> None:
    base = flip_world()
    for layer in graph_layers:
        provider = layer._provider
        kept = getattr(provider, "accessible_records_cache", None)
        redis = FakeRedis()
        if layer.name == "neo4j":      # the one store that reads this cache
            provider.accessible_records_cache = AccessibleRecordsCache(MagicMock(), redis, 300, True)
        set_limit = _set_neo4j if layer.name == "neo4j" else _set_arango
        settings = neo4j_settings if layer.name == "neo4j" else arango_settings
        team = (lambda u, p=provider: p._get_user_accessible_team_app_keys(u)) if layer.name == "arango" \
            else (lambda u, p=provider: p._get_user_accessible_team_app_ids(u))
        try:
            await layer.preload([base])
            ns = await layer._ns(base)
            prefix = layer._prefix[base]
            found: list[str] = []
            for i, (limit, model) in enumerate(STEPS):
                await set_limit(settings, ns.id("A"), limit, model)
                world = at_step(base, limit, model)
                graph = layer.shape(compile_world(world, prefix))
                found += [f"{layer.name} step {i} ({limit}): {b}" for b in await step_findings(provider, world, graph, ns, team)]
            if layer.name == "neo4j":
                assert redis.strings or redis.hashes, "the search cache was never filled: the flip did not run against it"
            assert not found, "\n".join(found[:40])
        finally:
            provider.accessible_records_cache = kept
            await layer.release([base])
