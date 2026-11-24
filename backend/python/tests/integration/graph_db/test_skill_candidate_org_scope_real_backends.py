"""A learning-loop candidate belongs to one organisation: another organisation cannot reject, approve or remove it.

`POST /api/v1/skills/candidates/{candidate_id}/reject` runs `SkillManager.reject_candidate` on a manager built for the
caller's organisation. The candidate id comes from the path, so an id of another organisation must answer exactly as
an id nobody holds ("not found", 404) and leave the other organisation's candidate in place. Real ArangoDB and Neo4j
(the backend-matrix graph legs); the PostgreSQL leg is `postgres/test_skill_candidate_org_scope.py`.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import SkillCandidate
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
log = logging.getLogger("skill-candidate-org-scope")


async def _provider(kind: str, monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    try:
        if kind == "neo4j":
            return await connect_neo4j(log, monkeypatch)
        return await connect_arango(log, f"skillcand_{uuid.uuid4().hex[:8]}")
    except Exception as exc:
        backend_unavailable(kind, exc)


def _manager_for(provider: IGraphDBProvider, org_id: str, user_id: str) -> SkillManager:
    """The route's manager for one org: only the attributes the candidate methods read when a store is bound."""
    store = GraphSkillStore(provider, org_id, user_id, visibility_scope=user_id)
    mgr = SkillManager.__new__(SkillManager)
    mgr._candidate_store = store
    return mgr


def _candidate(cid: str, name: str = "b-secret-skill") -> SkillCandidate:
    return SkillCandidate(candidate_id=cid, name=name, description="B", body="B body", created_at="0")


async def _world(kind: str, monkeypatch: pytest.MonkeyPatch) -> tuple[IGraphDBProvider, str, str, GraphSkillStore, str]:
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
async def test_org_a_cannot_reject_org_b_candidate(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_store_remove_is_scoped_to_the_store_org(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a caller that skips the manager's lookup cannot delete across orgs."""
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    await GraphSkillStore(p, org_a, "user-a").remove_candidate(cid)
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await store_b.remove_candidate(cid)  # own org still removes it
    assert await store_b.get_pending_candidates() == []
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_org_a_cannot_approve_org_b_candidate(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    with pytest.raises(RegistryError, match="not found"):
        await _manager_for(p, org_a, "user-a").approve_candidate(cid)
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_own_org_rejects_its_candidate(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    await _manager_for(p, org_b, "user-b").reject_candidate(cid)
    assert await store_b.get_pending_candidates() == []
    with pytest.raises(RegistryError, match="not found"):  # a second reject is a missing id
        await _manager_for(p, org_b, "user-b").reject_candidate(cid)
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_queue_with_another_orgs_candidate_id_does_not_overwrite_it(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    store_a = GraphSkillStore(p, org_a, "user-a")
    with pytest.raises(RegistryError, match="already exists"):
        await store_a.queue_candidate(_candidate(cid, name="a-overwrite"))
    assert [(c.candidate_id, c.name) for c in await store_b.get_pending_candidates()] == [(cid, "b-secret-skill")]
    assert await store_a.get_pending_candidates() == []
    # Re-queueing a candidate of the own org still replaces it.
    await store_b.queue_candidate(_candidate(cid, name="b-renamed"))
    assert [c.name for c in await store_b.get_pending_candidates()] == ["b-renamed"]
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_concurrent_foreign_rejects_never_remove_it(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    manager_a = _manager_for(p, org_a, "user-a")

    async def foreign() -> str:
        try:
            await manager_a.reject_candidate(cid)
        except RegistryError:
            return "not found"
        return "removed"

    own = asyncio.create_task(_manager_for(p, org_b, "user-b").reject_candidate(cid))
    results = await asyncio.gather(*[foreign() for _ in range(25)])
    assert set(results) == {"not found"}
    await own  # org B's own reject wins
    assert await store_b.get_pending_candidates() == []
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
@pytest.mark.parametrize("org", [None, ""])
async def test_a_store_without_an_org_removes_and_lists_nothing(kind: str, org: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    nowhere = GraphSkillStore(p, org, "user-x")
    assert await nowhere.get_pending_candidates() == []
    await nowhere.remove_candidate(cid)
    with pytest.raises(RegistryError, match="not found"):
        await _manager_for(p, org, "user-x").reject_candidate(cid)
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_a_stale_delete_bound_to_the_old_owner_cannot_remove_the_new_owners_candidate(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Org A held the id, it was removed, and org B queued the same id: A's delete still in flight matches the owner at write time."""
    p, org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    assert await p.delete_node_if_match(cid, "agentSkillCandidates", {"orgId": org_a}) is False
    assert [c.candidate_id for c in await store_b.get_pending_candidates()] == [cid]
    assert await p.delete_node_if_match(cid, "agentSkillCandidates", {"orgId": org_b}) is True
    assert await store_b.get_pending_candidates() == []
    await p.disconnect()


@pytest.mark.parametrize("kind", ["arango", "neo4j"])
async def test_requeue_is_one_owner_bound_replace_and_a_failed_write_keeps_the_old_candidate(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    p, _org_a, org_b, store_b, cid = await _world(kind, monkeypatch)
    deletes: list[str] = []
    real_delete = p.delete_node_if_match

    async def spy_delete(*args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        deletes.append(args[0])
        return await real_delete(*args, **kwargs)

    monkeypatch.setattr(p, "delete_node_if_match", spy_delete)
    await store_b.queue_candidate(_candidate(cid, name="b-renamed"))
    assert deletes == []  # no delete-then-insert: nothing a cancellation could split
    assert [c.name for c in await store_b.get_pending_candidates()] == ["b-renamed"]

    async def failing_replace(*_args: Any, **_kwargs: Any) -> bool:  # noqa: ANN401
        raise RuntimeError("write failed")

    monkeypatch.setattr(p, "update_node_if_match", failing_replace)
    with pytest.raises(RuntimeError, match="write failed"):
        await store_b.queue_candidate(_candidate(cid, name="b-never"))
    assert [c.name for c in await store_b.get_pending_candidates()] == ["b-renamed"]  # the old candidate is still there
    assert org_b
    await p.disconnect()

