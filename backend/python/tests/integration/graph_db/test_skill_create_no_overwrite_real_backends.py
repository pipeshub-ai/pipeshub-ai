"""A skill create never replaces another user's skill, on a real Neo4j and a real ArangoDB.

`GraphSkillStore.create_skill` inserts `{org}_{name}` with `create_node_if_absent`. The unit
suite drives it through an in-memory fake; this file runs the shipped providers (the strict
ArangoDB collection schema included), so a document the product could not write, or a race
the store does not settle, fails here. It also runs the owner lifecycle, builtin seeding of
a new org, the builtin claim a member cannot store, and the history of a name created again.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD. SKILL_IT_SEED picks the race shape.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillAlreadyExistsError,
    SkillConflictError,
    SkillPermissionError,
    SkillStatus,
)
from app.agents.agent_loop.skills.builtin_seeder import (
    SEED_IDENTITY,
    BuiltinSkillSeeder,
)
from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.skill_stores import skill_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agents.agent_loop.skills.graph_store import GraphSkillStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "skill_no_overwrite_it"
SKILLS = CollectionNames.AGENT_SKILLS.value
VERSIONS = CollectionNames.AGENT_SKILL_VERSIONS.value
SEED = int(os.environ.get("SKILL_IT_SEED") or random.SystemRandom().randrange(1 << 30))
PACKS = Path(__file__).resolve().parents[3] / "app" / "agents" / "agent_loop" / "skills" / "builtin_packs"

logger = logging.getLogger("skill-no-overwrite-it")


def _md(name: str, marker: str, requires: str | None = None, extra: str = "") -> str:
    ext = (f"    requires:\n      - {requires}\n" if requires else "") + extra
    meta = f"metadata:\n  agent-loop:\n{ext}" if ext else ""
    return f"---\nname: {name}\ndescription: Use when doing {marker}\n{meta}---\n\nBody written by {marker}.\n"


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        org = f"it-org-{uuid.uuid4().hex[:10]}"
        yield provider, org
        if not isinstance(provider, Neo4jProvider):  # Neo4j's DETACH DELETE below takes the edges with the nodes
            for edges in ("permission", "agentSkillRelation", "agentHasSkill"):
                await provider.http_client.execute_aql(
                    f"FOR e IN {edges} FILTER STARTS_WITH(e._from, @p) OR STARTS_WITH(e._to, @p) REMOVE e IN {edges}",
                    {"p": f"{SKILLS}/{org}_"},
                )
        for collection in (SKILLS, VERSIONS):
            if isinstance(provider, Neo4jProvider):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(collection)}) WHERE n.orgId = $o DETACH DELETE n",
                    parameters={"o": org},
                )
            else:
                await provider.http_client.execute_aql(
                    f"FOR n IN {collection} FILTER n.orgId == @o REMOVE n IN {collection}", {"o": org},
                )


def _store(
    provider: IGraphDBProvider, org: str, user: str, *, scoped: bool = True, admin: bool = False,
) -> GraphSkillStore:
    return skill_store(provider, org, user, visibility_scope=user if scoped else None, is_admin=admin)


async def test_create_lands_and_a_second_creator_is_refused(graph) -> None:
    provider, org = graph
    owner = _store(provider, org, "user-a")
    await owner.create_skill("base-skill", _md("base-skill", "base"))
    await owner.create_skill("deploy", _md("deploy", "owner-a", requires="base-skill"), resources={"notes.md": "a notes"})
    await owner.update_skill("deploy", _md("deploy", "owner-a-v2", requires="base-skill"))
    before = await provider.get_document(f"{org}_deploy", SKILLS)
    assert before is not None and before["createdBy"] == "user-a"

    for scoped in (True, False):
        with pytest.raises(SkillAlreadyExistsError) as exc:
            await _store(provider, org, "user-b", scoped=scoped).create_skill("deploy", _md("deploy", "other-b"))
        assert "user-a" not in str(exc.value) and "owner-a" not in str(exc.value)

    assert await provider.get_document(f"{org}_deploy", SKILLS) == before
    assert [v.version for v in await owner.list_versions("deploy")] == ["1.0.0"]
    skill = await owner.get_skill("deploy")
    assert skill is not None and "owner-a-v2" in skill.body


async def test_n_parallel_creates_of_one_name_have_exactly_one_winner(graph) -> None:
    provider, org = graph
    rng = random.Random(SEED)
    users = [f"user-{i}" for i in range(rng.randint(6, 12))]
    rng.shuffle(users)
    name = f"race-{rng.randrange(1 << 20):05x}"

    results = await asyncio.gather(
        *(_store(provider, org, u, scoped=rng.random() < 0.5).create_skill(name, _md(name, f"marker-{u}")) for u in users),
        return_exceptions=True,
    )
    winners = [u for u, r in zip(users, results, strict=True) if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(winners) == 1, (SEED, results)
    assert all(isinstance(e, SkillAlreadyExistsError) for e in errors), (SEED, errors)

    doc = await provider.get_document(f"{org}_{name}", SKILLS)
    assert doc["createdBy"] == winners[0] and f"marker-{winners[0]}" in doc["content"], SEED
    owners = await provider.get_edges_to_node(f"{SKILLS}/{org}_{name}", CollectionNames.PERMISSION.value)
    # Neo4j links an edge only to an existing user node, and this file seeds none.
    assert len(owners) == (0 if isinstance(provider, Neo4jProvider) else 1), (SEED, owners)


async def test_owner_lifecycle_still_works(graph) -> None:
    provider, org = graph
    owner = _store(provider, org, "user-a")
    await owner.create_skill("life", _md("life", "v1"))
    await owner.update_skill("life", _md("life", "v2"))
    await owner.rollback("life", "1.0.0")
    assert await owner.write_resource("life", "extra.md", "more") is True
    assert await owner.set_skill_status("life", SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE) is True
    with pytest.raises(SkillPermissionError):
        await _store(provider, org, "user-b", scoped=False).update_skill("life", _md("life", "changed"))
    assert await owner.delete_skill("life") is True
    assert await provider.get_document(f"{org}_life", SKILLS) is None


async def test_an_admin_the_route_vetted_moderates_but_does_not_edit(graph) -> None:
    provider, org = graph
    await _store(provider, org, "user-a").create_skill("moderated", _md("moderated", "owner-a"))
    admin = _store(provider, org, "admin-1", scoped=False, admin=True)

    with pytest.raises(SkillPermissionError):
        await admin.update_skill("moderated", _md("moderated", "admin-edit"))
    assert await admin.set_skill_status("moderated", SkillStatus.DISABLED) is True
    assert await admin.delete_skill("moderated") is True


async def test_a_new_org_is_seeded_with_every_pack(graph, caplog) -> None:
    provider, org = graph
    seeder = BuiltinSkillSeeder(str(PACKS))

    settled = await seeder.sync(skill_store(provider, org, SEED_IDENTITY))

    docs = await provider.get_nodes_by_filters(SKILLS, {"orgId": org})
    assert settled is True
    assert {d["name"] for d in docs} == set(seeder.pack_versions)
    assert {d["source"] for d in docs} == {"builtin"} and {d["createdBy"] for d in docs} == {SEED_IDENTITY}
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
    assert await seeder.sync(skill_store(provider, org, SEED_IDENTITY)) is True


async def test_a_member_cannot_store_a_builtin(graph) -> None:
    provider, org = graph
    claim = _md("claimed", "member", extra="    source: builtin\n    pack_name: claimed\n    pack_version: 9.9.9\n")
    with pytest.raises(RegistryError):
        await _store(provider, org, "user-a").create_skill("claimed", claim)
    await _store(provider, org, "user-a").create_skill("claimed", claim.replace("source: builtin", "source: manual"))

    doc = await provider.get_document(f"{org}_claimed", SKILLS)
    assert doc["source"] == "manual" and not doc.get("packName") and not doc.get("packVersion")
    assert await _store(provider, org, "user-b").list_skills() == []


async def test_a_name_created_again_starts_a_new_history(graph) -> None:
    provider, org = graph
    old = _store(provider, org, "user-a")
    await old.create_skill("reused", _md("reused", "old-owner"))
    await old.update_skill("reused", _md("reused", "old-owner-v2"))
    assert await old.delete_skill("reused") is True
    archived = await provider.get_nodes_by_filters(VERSIONS, {"orgId": org, "name": "reused"})
    assert len(archived) == 2  # the edit's, and the last content, archived by the delete

    new = _store(provider, org, "user-b")
    await new.create_skill("reused", _md("reused", "new-owner"))
    assert await new.list_versions("reused") == []
    assert await new.get_version("reused", "1.0.0") is None
    with pytest.raises(RegistryError, match="not found"):
        await new.rollback("reused", "1.0.0")
    await new.update_skill("reused", _md("reused", "new-owner-v2"))

    versions = await provider.get_nodes_by_filters(VERSIONS, {"orgId": org, "name": "reused"})
    assert len(versions) == 3
    assert any("old-owner" in v["content"] for v in versions)


async def test_a_stale_update_cannot_replace_a_skill_created_in_between(graph) -> None:
    provider, org = graph
    owner = _store(provider, org, "user-a")
    await owner.create_skill("swapped", _md("swapped", "old-owner"))
    stale = await provider.get_document(f"{org}_swapped", SKILLS)

    assert await owner.delete_skill("swapped") is True
    await _store(provider, org, "user-b").create_skill("swapped", _md("swapped", "new-owner"))
    # The worst case: the new skill carries the token the stale read holds, so only the generation tells them apart.
    assert await provider.update_node(f"{org}_swapped", SKILLS, {"updatedAtTimestamp": stale["updatedAtTimestamp"]})
    replacement = await provider.get_document(f"{org}_swapped", SKILLS)
    assert replacement["updatedAtTimestamp"] == stale["updatedAtTimestamp"]
    assert replacement["generation"] != stale["generation"]

    with pytest.raises((SkillConflictError, RegistryError)):
        await owner._commit_skill_update(  # the write an update makes after its ownership read
            "swapped", {**stale, "content": "stale write"}, None, read=stale,
        )
    doc = await provider.get_document(f"{org}_swapped", SKILLS)
    assert doc["createdBy"] == "user-b" and "new-owner" in doc["content"]
