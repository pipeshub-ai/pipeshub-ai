# ruff: noqa: ANN201, ANN202
"""The scope rewrite against each real vector store: Qdrant, OpenSearch, Redis and pgvector. The graph is the in-memory world of the permission
kit; the points are real. For scenarios of every combining rule:

* a rewritten subtree is hidden while it is rewritten and exact after (the oracle gate of `tests/permissions`), and what the job wrote is
  searchable: the store's own search with the scope filter returns exactly the points the filter tree admits (OpenSearch needs its index
  refreshed first: the job asks for it before it lifts a hide);
* a second organisation's point with the same virtual record id is not touched;
* a run killed half way and run again ends exact;
* a held-up writer cannot write over a newer rewrite, by the store's fence where it has one.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d (pgvector: see test_pgvector_pending_corpus.py).
"""

from __future__ import annotations

import asyncio
import contextvars
import copy

import pytest

from app.core.org_context import for_org
from app.services.permissions.scope_fields import ScopeFields
from app.services.vector_db.models import VectorPoint
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import make_dense
from tests.permissions.model import Mutation as M
from tests.permissions.rewrite_kit import Env, Killed, Log, Tap, held_up_writer
from tests.permissions.scenario import WorldStore
from tests.permissions.scope_corpus import SCENARIOS
from tests.permissions.stamp_world import ORG
from tests.permissions.store_harness import read_stamps

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900), pytest.mark.asyncio(loop_scope="module")]

OTHER_ORG = "cd" * 12
PICKS = ("DRIVE-C-07", "CONF-C-03", "LIN-C-05", "SP-C-12", "OD-C-01")
CHOSEN = [s for s in SCENARIOS if s.id in PICKS] or SCENARIOS[:4]
CONF_C_03 = next(s for s in SCENARIOS if s.id == "CONF-C-03")      # a change that moves a stamp
HR = WorldStore().world("golden/confluence_hr")


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


class _Contract:
    store: str

    async def make(self, svc, world, **kw) -> Env:
        return await Env.make(world, svc, collection=make_collection(self.store), settle_seconds=60.0, **kw)

    async def drop(self, svc, env: Env) -> None:
        env.close()
        await svc.delete_collection(env.loaded.collection)

    @pytest.mark.parametrize("scenario", CHOSEN, ids=[s.id for s in CHOSEN])
    async def test_a_rewritten_subtree_is_hidden_meanwhile_exact_after_and_searchable(self, vector_service, scenario) -> None:
        env = await self.make(vector_service, scenario.world, chunks=2)
        try:
            before, after = Log(), Log()
            await env.change(scenario.mutations)
            await env.audit(before)
            await env.settle()
            await env.audit(after)
            await env.store_audit(after)
            assert not before.leaks and not before.pending_admits and not after.leaks, (before.leaks[:3], before.pending_admits[:3], after.leaks[:3])
            assert not after.search_mismatch, after.search_mismatch[:3]
            assert not await env.problems()
        finally:
            await self.drop(vector_service, env)

    async def test_a_second_organisations_point_is_not_touched(self, vector_service) -> None:
        env = await self.make(vector_service, HR)
        try:
            mine = (await vector_service.retrieve_points(env.loaded.collection, env.loaded.points["w:v-page:Handbook"][:1]))[0]
            theirs = copy.deepcopy(mine.payload)
            theirs["metadata"]["orgId"] = OTHER_ORG
            theirs.update({"scopeStamp": "union", "pathIds": ["theirs"], "aclScope": ["theirs"], "connectorIds": ["theirs"], "recordGroupIds": ["g-theirs"]})
            pid = "00000000-0000-4000-8000-0000000000aa"
            await in_org(OTHER_ORG, vector_service.upsert_points(env.loaded.collection, [VectorPoint(id=pid, dense_vector=make_dense([1.0, 9.0]), payload=theirs)]))
            await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
            await env.settle()
            assert not await env.problems()
            got = await in_org(OTHER_ORG, vector_service.retrieve_points(env.loaded.collection, [pid]))
            assert got and got[0].payload["pathIds"] == ["theirs"] and got[0].payload["recordGroupIds"] == ["g-theirs"]
        finally:
            await self.drop(vector_service, env)

    async def test_a_run_killed_half_way_and_run_again_ends_exact(self, vector_service) -> None:
        env = await self.make(vector_service, HR, chunks=2)
        try:
            await env.change((M("moved", "page:Handbook", "space:ALL", "space:ALL"),))
            seen = {"n": 0, "live": True}

            async def die(name: str) -> None:
                if name == "update_payloads_by_ids":
                    seen["n"] += 1
                    if seen["live"] and seen["n"] == 1:
                        raise Killed

            tap = Tap(env, _no, die)
            with pytest.raises(Killed):
                await env.settle()
            tap.remove()
            hidden = Log()
            await env.audit(hidden)
            assert env.pending() and not hidden.leaks and not hidden.pending_admits
            seen["live"] = False
            await env.settle()
            assert not await env.problems()
        finally:
            await self.drop(vector_service, env)

    async def test_a_held_up_writer_cannot_write_over_a_newer_rewrite(self, vector_service, monkeypatch) -> None:
        for layer in ("generation_check", "store_fence") if vector_service.generation_fence else ("generation_check",):
            env = await self.make(vector_service, HR)
            try:
                await held_up_writer(env, layer, monkeypatch)
            finally:
                await self.drop(vector_service, env)
                monkeypatch.undo()
                monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def in_org(org: str, call):
    """Run a store call for another organisation than the test's: in a context of its own, as a request of that organisation would."""

    async def run():
        with for_org(org, job="test"):
            return await call

    return await asyncio.create_task(run(), context=contextvars.Context())


async def _no(_: str) -> None:
    return None


class TestQdrantRewrite(_Contract):
    store = "qdrant"

    @pytest.fixture
    async def vector_service(self, qdrant_service):
        return qdrant_service


class TestOpenSearchRewrite(_Contract):
    store = "opensearch"

    @pytest.fixture
    async def vector_service(self, opensearch_service):
        return opensearch_service

    async def test_a_point_indexed_but_not_yet_searchable_is_rewritten_too(self, vector_service) -> None:
        """A point written a moment ago (OpenSearch searches it at the next refresh) is in neither the scroll nor the count: the job asks the
        store to publish before it reads, or the root would be released with that point on its old stamp."""
        env = await self.make(vector_service, CONF_C_03.world)
        try:
            await env.change(CONF_C_03.mutations)
            assert env.changed
            vrid = sorted(env.changed)[0]      # a record whose stamp the change changes
            sibling = (await vector_service.retrieve_points(env.loaded.collection, env.loaded.points[vrid][:1]))[0].payload
            pid = "00000000-0000-4000-8000-0000000000bb"
            await in_org(ORG, vector_service.upsert_points(env.loaded.collection, [VectorPoint(id=pid, dense_vector=make_dense([1.0, 9.0]), payload=copy.deepcopy(sibling))]))
            await env.settle()
            assert not await env.problems()
            after = (await read_stamps(vector_service, env.loaded))[vrid]
            late = (await vector_service.retrieve_points(env.loaded.collection, [pid]))[0].payload
            assert ScopeFields.from_payload(late) == after
        finally:
            await self.drop(vector_service, env)


class TestRedisRewrite(_Contract):
    store = "redis"

    @pytest.fixture
    async def vector_service(self, redis_service):
        return redis_service
