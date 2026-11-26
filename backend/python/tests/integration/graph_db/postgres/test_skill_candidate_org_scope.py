"""A learning-loop candidate belongs to one organisation: another organisation cannot reject, approve or remove it.

The PostgreSQL leg of `../test_skill_candidate_org_scope_real_backends.py`. The route
(`POST /api/v1/skills/candidates/{candidate_id}/reject`) builds the manager for the caller's org and takes the
id from the path, so an id of another org must answer as an id nobody holds and leave the candidate alone.
Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills.graph_store import GraphSkillStore, SkillVisibility

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def orgs(pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, str]]:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org_a, org_b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    yield org_a, org_b
    for org in (org_a, org_b):
        with org_ctx(org):
            await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


def _manager_for(provider, org_id: str, user_id: str) -> SkillManager:
    """The route's manager for one org: only the attributes the candidate methods read when a store is bound."""
    mgr = SkillManager.__new__(SkillManager)
    mgr._candidate_store = GraphSkillStore(provider, org_id, user_id, visibility=SkillVisibility.of([user_id]))
    return mgr


async def _queue(provider, org_ctx, org: str, cid: str) -> None:
    with org_ctx(org):
        await GraphSkillStore(provider, org, "user-b").queue_candidate(
            SkillCandidate(candidate_id=cid, name="b-secret-skill", description="B", body="B body", created_at="0"),
        )


async def _pending(provider, org_ctx, org: str) -> list[str]:
    with org_ctx(org):
        return [c.candidate_id for c in await GraphSkillStore(provider, org, "user-b").get_pending_candidates()]


async def test_org_a_cannot_reject_or_approve_org_b_candidate(pg_provider, orgs, org_ctx) -> None:
    org_a, org_b = orgs
    cid = str(uuid.uuid4())
    await _queue(pg_provider, org_ctx, org_b, cid)
    assert await _pending(pg_provider, org_ctx, org_a) == []  # negative control: org A cannot see it

    with org_ctx(org_a):
        with pytest.raises(RegistryError, match="not found") as foreign:
            await _manager_for(pg_provider, org_a, "user-a").reject_candidate(cid)
        nobody = str(uuid.uuid4())
        with pytest.raises(RegistryError, match="not found") as missing:
            await _manager_for(pg_provider, org_a, "user-a").reject_candidate(nobody)
        assert str(foreign.value).replace(cid, "ID") == str(missing.value).replace(nobody, "ID")
        with pytest.raises(RegistryError, match="not found"):
            await _manager_for(pg_provider, org_a, "user-a").approve_candidate(cid)
        # Even a caller that skips the manager's lookup removes nothing outside its own org.
        await GraphSkillStore(pg_provider, org_a, "user-a").remove_candidate(cid)
    assert await _pending(pg_provider, org_ctx, org_b) == [cid]


async def test_own_org_rejects_its_candidate(pg_provider, orgs, org_ctx) -> None:
    _org_a, org_b = orgs
    cid = str(uuid.uuid4())
    await _queue(pg_provider, org_ctx, org_b, cid)
    with org_ctx(org_b):
        await _manager_for(pg_provider, org_b, "user-b").reject_candidate(cid)
        with pytest.raises(RegistryError, match="not found"):
            await _manager_for(pg_provider, org_b, "user-b").reject_candidate(cid)
    assert await _pending(pg_provider, org_ctx, org_b) == []


@pytest.mark.parametrize("org", [None, ""])
async def test_graph_user_keys_refuse_a_missing_org(pg_provider, orgs, org_ctx, org) -> None:
    org_a, _org_b = orgs
    with org_ctx(org_a):
        assert await pg_provider.batch_upsert_nodes([{"_key": "u-1", "orgId": org_a, "userId": "mongo-1", "email": "u1@example.com"}], "users")
        assert await pg_provider.get_graph_user_keys_by_mongo_user_ids(["mongo-1"], org_a, chunk_size=10) == {"mongo-1": "u-1"}
        with pytest.raises(ValueError, match="org_id is required"):
            await pg_provider.get_graph_user_keys_by_mongo_user_ids(["mongo-1"], org, chunk_size=10)
        with pytest.raises(ValueError, match="Users not found"):  # a user of another org is not found
            await pg_provider.get_graph_user_keys_by_mongo_user_ids(["mongo-1"], "another-org", chunk_size=10)


async def test_queue_with_another_orgs_candidate_id_does_not_overwrite_it(pg_provider, orgs, org_ctx) -> None:
    org_a, org_b = orgs
    cid = str(uuid.uuid4())
    await _queue(pg_provider, org_ctx, org_b, cid)
    with org_ctx(org_a):  # the same id in another org is another row (the key includes the org)
        await GraphSkillStore(pg_provider, org_a, "user-a").queue_candidate(
            SkillCandidate(candidate_id=cid, name="a-own", description="A", body="A body", created_at="0"),
        )
    assert await _pending(pg_provider, org_ctx, org_b) == [cid]
    with org_ctx(org_b):
        assert [c.name for c in await GraphSkillStore(pg_provider, org_b, "user-b").get_pending_candidates()] == ["b-secret-skill"]


async def test_a_delete_bound_to_an_owner_removes_only_that_owners_candidate(pg_provider, orgs, org_ctx) -> None:
    org_a, org_b = orgs
    cid = str(uuid.uuid4())
    await _queue(pg_provider, org_ctx, org_b, cid)
    with org_ctx(org_b):
        assert await pg_provider.delete_node_if_match(cid, "agentSkillCandidates", {"orgId": org_a}) is False  # the stored owner is not org A
        assert await _pending(pg_provider, org_ctx, org_b) == [cid]
        assert await pg_provider.delete_node_if_match(cid, "agentSkillCandidates", {"orgId": org_b}) is True
    assert await _pending(pg_provider, org_ctx, org_b) == []


async def test_requeue_replaces_the_own_candidate_and_never_another_orgs(pg_provider, orgs, org_ctx) -> None:
    org_a, org_b = orgs
    cid = str(uuid.uuid4())
    await _queue(pg_provider, org_ctx, org_b, cid)
    with org_ctx(org_b):
        await GraphSkillStore(pg_provider, org_b, "user-b").queue_candidate(
            SkillCandidate(candidate_id=cid, name="b-renamed", description="B", body="B body", created_at="0"),
        )
        assert [c.name for c in await GraphSkillStore(pg_provider, org_b, "user-b").get_pending_candidates()] == ["b-renamed"]
    with org_ctx(org_a):  # the same id in org A is a row of its own (the key includes the org)
        await GraphSkillStore(pg_provider, org_a, "user-a").queue_candidate(
            SkillCandidate(candidate_id=cid, name="a-own", description="A", body="A body", created_at="0"),
        )
    assert await _pending(pg_provider, org_ctx, org_b) == [cid]

