"""A learning-loop candidate belongs to one organisation: another organisation cannot reject, approve or remove it.

`POST /api/v1/skills/candidates/{candidate_id}/reject` runs `SkillManager.reject_candidate` on a manager built for the
caller's organisation. The candidate id comes from the path, so an id of another organisation must answer exactly as
an id nobody holds ("not found", 404) and leave the other organisation's candidate in place. Real ArangoDB and Neo4j
(the backend-matrix graph legs); the PostgreSQL leg is `postgres/test_skill_candidate_org_scope.py`.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from tests.integration.real_graph import connect_arango, connect_neo4j

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
log = logging.getLogger("skill-candidate-org-scope")


async def _provider(kind, monkeypatch):
    if kind == "neo4j":
        return await connect_neo4j(log, monkeypatch)
    return await connect_arango(log, f"skillcand_{uuid.uuid4().hex[:8]}")


def _manager_for(provider, org_id: str, user_id: str) -> SkillManager:
    """The route's manager for one org: only the attributes the candidate methods read when a store is bound."""
    store = GraphSkillStore(provider, org_id, user_id, visibility_scope=user_id)
    mgr = SkillManager.__new__(SkillManager)
    mgr._candidate_store = store
    return mgr


def _candidate(cid: str, name: str = "b-secret-skill") -> SkillCandidate:
    return SkillCandidate(candidate_id=cid, name=name, description="B", body="B body", created_at="0")


async def _world(kind, monkeypatch):
    p = await _provider(kind, monkeypatch)
    org_a, org_b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    store_b = GraphSkillStore(p, org_b, "user-b")
    cid = str(uuid.uuid4())
    await store_b.queue_candidate(_candidate(cid))
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    # Negative control: org A cannot even see it.
    assert await GraphSkillStore(p, org_a, "user-a").get_pending_candidates() == []
    return p, org_a, org_b, store_b, cid


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_org_a_cannot_reject_org_b_candidate(kind, monkeypatch):
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    nobody = str(uuid.uuid4())
    with pytest.raises(RegistryError) as foreign:
        await _manager_for(p, org_a, "user-a").reject_candidate(cid)
    with pytest.raises(RegistryError) as missing:
        await _manager_for(p, org_a, "user-a").reject_candidate(nobody)
    # No existence oracle: another org's id answers exactly like an id nobody holds (the route maps both to 404).
    assert str(foreign.value).replace(cid, "ID") == str(missing.value).replace(nobody, "ID")
    assert "not found" in str(foreign.value).lower()
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_store_remove_is_scoped_to_the_store_org(kind, monkeypatch):
    """Even a caller that skips the manager's lookup cannot delete across orgs."""
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    await GraphSkillStore(p, org_a, "user-a").remove_candidate(cid)
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await store_b.remove_candidate(cid)  # own org still removes it
    assert await store_b.get_pending_candidates() == []
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_org_a_cannot_approve_org_b_candidate(kind, monkeypatch):
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    with pytest.raises(RegistryError, match="not found"):
        await _manager_for(p, org_a, "user-a").approve_candidate(cid)
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_own_org_rejects_its_candidate(kind, monkeypatch):
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    await _manager_for(p, org_b, "user-b").reject_candidate(cid)
    assert await store_b.get_pending_candidates() == []
    with pytest.raises(RegistryError, match="not found"):  # a second reject is a missing id
        await _manager_for(p, org_b, "user-b").reject_candidate(cid)
    await p.disconnect()
