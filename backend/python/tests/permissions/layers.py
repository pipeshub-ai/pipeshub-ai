"""Graph-check access layers: load a world into a real store and ask `check_access`.

Reuses `tests/integration/graph_permissions/loaders.py` for the raw inserts and
the provider fixtures of that harness. Worlds are loaded once, side by side
under different id prefixes, so a whole corpus costs one load per backend.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING

import aiohttp
from neo4j import AsyncGraphDatabase

from tests.integration.graph_permissions.loaders import (
    EDGE_TARGETS,
    NODE_TARGETS,
    arango_shape,
    load_into_arango,
    load_into_neo4j,
)

from .graph import Graph, Namespace, compile_world

if TYPE_CHECKING:
    from .model import World

KNOWN_RULES = (None, "OPEN", "STRICT", "RESTRICTED")
# The permission-type values Arango's edge schema accepts (schema/arango/edges.py); ANYONE is not one of them.
ARANGO_GRANT_TYPES = {None, "USER", "GROUP", "DOMAIN", "TEAM", "ORG", "ROLE", "ORGANIZATION"}
LoadFn = Callable[[list, list], Awaitable[None]]
UnloadFn = Callable[[list[str]], Awaitable[None]]


def arango_graph(graph: Graph) -> Graph:
    """The graph as Arango stores it: also without a node whose accessRule is unknown, and without a
    permission edge whose type its edge schema rejects."""
    nodes, edges = arango_shape(graph["nodes"], graph["edges"])
    bad = {n["id"] for n in nodes if n["props"].get("accessRule") not in KNOWN_RULES}
    return {
        "nodes": [n for n in nodes if n["id"] not in bad],
        "edges": [e for e in edges if e["from"] not in bad and e["to"] not in bad
                  and not (e["type"] == "PERMISSION" and e["props"].get("type") not in ARANGO_GRANT_TYPES)],
    }


def _same(graph: Graph) -> Graph:
    return graph


class GraphCheckLayer:
    """`check_access` of a live provider (Neo4j or Arango) as an access layer."""

    def __init__(self, name: str, provider: object, load: LoadFn, unload: UnloadFn, narrow: bool) -> None:
        self.name = name
        self._provider = provider
        self._load = load
        self._unload = unload
        self._narrow = narrow
        self._prefix: dict[World, str] = {}
        self._counter = itertools.count()  # never reused, so a released prefix cannot collide with a later load

    def shape(self, graph: Graph) -> Graph:
        return arango_graph(graph) if self._narrow else _same(graph)

    def _compiled(self, world: World) -> Graph:
        return self.shape(compile_world(world, self._prefix[world]))

    async def preload(self, worlds: Iterable[World]) -> None:
        """Load every not yet loaded world in one batch."""
        fresh = [w for w in dict.fromkeys(worlds) if w not in self._prefix]
        for w in fresh:
            self._prefix[w] = f"w{next(self._counter)}"
        nodes = [n for w in fresh for n in self._compiled(w)["nodes"]]
        edges = [e for w in fresh for e in self._compiled(w)["edges"]]
        if nodes:
            await self._load(nodes, edges)

    async def release(self, worlds: Iterable[World]) -> None:
        """Delete the worlds from the store. A store larger than the engine's page cache slows every query,
        so long runs load and release in chunks."""
        gone = [w for w in dict.fromkeys(worlds) if w in self._prefix]
        if gone:
            await self._unload([f"{self._prefix.pop(w)}-" for w in gone])

    async def _ns(self, world: World) -> Namespace:
        if world not in self._prefix:
            await self.preload([world])
        return Namespace(self._prefix[world])

    async def admitted(self, world: World, viewer: str, node_ids: list[str]) -> set[str]:
        ns = await self._ns(world)
        found = await self._provider.check_access(  # type: ignore[attr-defined]
            ns.id(viewer), ns.org, node_ids=[ns.id(n) for n in node_ids])
        # A placeholder stub is navigable, not readable content: check_access lists it among the reachable ids but not as access
        return {ns.local(n) for n in found.node_ids if n not in found.placeholder_ids}

    async def cited(
        self, world: World, viewer: str, vrids: list[str], indexed_only: bool
    ) -> dict[str, str | None]:
        ns = await self._ns(world)
        stored = [ns.id(v) for v in vrids]
        found = await self._provider.check_access(  # type: ignore[attr-defined]
            ns.id(viewer), ns.org, virtual_record_ids=stored, indexed_only=indexed_only)
        by_vrid = found.records_by_vrid
        return {v: (None if by_vrid.get(s) is None else ns.local(by_vrid[s])) for v, s in zip(vrids, stored, strict=True)}


def neo4j_layer(provider: object, settings: dict) -> GraphCheckLayer:
    async def load(nodes: list, edges: list) -> None:
        await load_into_neo4j(settings, nodes, edges)

    async def unload(prefixes: list[str]) -> None:
        driver = AsyncGraphDatabase.driver(settings["uri"], auth=(settings["username"], settings["password"]))
        try:
            async with driver.session(database=settings["database"]) as session:
                await session.run(
                    "MATCH (n) WHERE any(p IN $prefixes WHERE n.id STARTS WITH p) DETACH DELETE n", prefixes=prefixes)
        finally:
            await driver.close()

    return GraphCheckLayer("neo4j", provider, load, unload, narrow=False)


def arango_layer(provider: object, settings: dict) -> GraphCheckLayer:
    async def load(nodes: list, edges: list) -> None:
        await load_into_arango(settings, nodes, edges)

    async def unload(prefixes: list[str]) -> None:
        base, db = settings["url"].rstrip("/"), settings["db"]
        auth = aiohttp.BasicAuth(settings["username"], settings["password"])
        match = "LENGTH(FOR p IN @prefixes FILTER STARTS_WITH({key}, p) LIMIT 1 RETURN 1) > 0"
        async with aiohttp.ClientSession(auth=auth) as session:
            for collection, key in [*((c, "d._key") for c in NODE_TARGETS.values()),
                                    *((c, "PARSE_IDENTIFIER(d._from).key") for c in [*EDGE_TARGETS.values(), "recordLinks"])]:
                query = f"FOR d IN {collection} FILTER {match.format(key=key)} REMOVE d IN {collection}"
                async with session.post(f"{base}/_db/{db}/_api/cursor",
                                        json={"query": query, "bindVars": {"prefixes": prefixes}}) as resp:
                    if resp.status not in (200, 201, 404):  # 404: the edge collection was never created
                        raise RuntimeError(f"unload from {collection} failed: {resp.status} {await resp.text()}")

    return GraphCheckLayer("arango", provider, load, unload, narrow=True)
