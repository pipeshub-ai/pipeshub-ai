"""A connector's access limit flips null -> CREATOR -> ORG -> null -> CREATOR in place on PostgreSQL, with the connector served
from kg (copied, stamped and promoted) and the projection never drained after a flip (the worst case: the restamp not landed
yet), then again drained. After each flip the access check equals the oracle on every viewer x node pair, the search access
list names no record of a limited connector the oracle denies, and entity search reaches the limited connector exactly as its limit says.
(Neo4j and ArangoDB: ``tests/permissions/test_access_limit_flip.py``.)"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres.kg.drainer import drain_org
from tests.permissions.graph import Namespace, compile_world
from tests.permissions.test_access_limit_flip import (
    STEPS,
    at_step,
    flip_world,
    step_findings,
)

from .test_conformance import _hex, _props
from .test_kg_read import KgLayer, _admin, _bound

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import AccessCheck

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


class _Hexed:
    """The provider as the flip steps ask it, under the world's hexed org (the store's org id)."""

    def __init__(self, provider, org: str) -> None:
        self._p, self._org = provider, org

    async def check_access(self, user: str, _org: str, **kw: object) -> AccessCheck:
        with _bound(self._org):
            return await self._p.check_access(user, self._org, **kw)

    async def get_accessible_virtual_record_ids(self, user: str, _org: str, **kw: object) -> dict[str, str]:
        with _bound(self._org):
            return await self._p.get_accessible_virtual_record_ids(user, self._org, **kw)

    async def get_entity_access_context(self, user: str, _org: str) -> dict | None:
        with _bound(self._org):
            return await self._p.get_entity_access_context(user, self._org)


@pytest.mark.parametrize("drained", [False, True], ids=["projection-stale", "projection-drained"])
async def test_flipping_the_limit_is_seen_at_once_when_kg_serves_the_connector(pg_provider, pg_db, drained: bool) -> None:
    base = flip_world()
    layer = KgLayer(pg_provider, pg_db)
    try:
        await layer.preload([base])
        assert layer.promoted["promoted"], "no connector was promoted: kg never served the check"
        prefix = layer._prefix[base]
        ns = Namespace(prefix)
        org = _hex(ns.org)
        found: list[str] = []
        for i, (limit, model) in enumerate(STEPS):
            fields = _props({"accessLimit": limit, "permissionModel": model})
            await _admin("UPDATE graph.nodes SET doc = (doc - 'accessLimit' - 'permissionModel') || $3::jsonb WHERE org_id = $1 AND coll = 'apps' AND id = $2",
                         org, ns.id("A"), json.dumps(fields))
            if drained:
                await drain_org(pg_db, org)
            world = at_step(base, limit, model)
            graph = compile_world(world, prefix)
            found += [f"postgres step {i} ({limit}): {b}" for b in await step_findings(_Hexed(pg_provider, org), world, graph, ns)]
        assert not found, "\n".join(found[:40])
    finally:
        await layer.release(list(layer._prefix))
