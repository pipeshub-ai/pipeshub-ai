"""A skill create never replaces another user's skill, on the PostgreSQL graph provider with schema validation on.

`GraphSkillStore.create_skill` inserts `{org}_{name}` with `create_node_if_absent`. N creators of one name race:
exactly one lands and its document is intact. Also the generic-id contract of `create_node_if_absent` on a
collection with a strict schema, the owner lifecycle, the builtin claim a member cannot store, and the history
of a name that is created again. Needs a migrated database: PG_CORE_URL, PG_POOLER=none. SKILL_IT_SEED picks the race.
"""

from __future__ import annotations

import asyncio
import os
import random
import uuid
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    SkillAlreadyExistsError,
    SkillPermissionError,
    SkillStatus,
)
from tests.support.skill_stores import skill_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agents.agent_loop.skills.graph_store import GraphSkillStore

pytestmark = pytest.mark.integration

SEED = int(os.environ.get("SKILL_IT_SEED") or random.SystemRandom().randrange(1 << 30))


def _md(name: str, marker: str, extra: str = "") -> str:
    meta = f"metadata:\n  agent-loop:\n{extra}" if extra else ""
    return f"---\nname: {name}\ndescription: Use when doing {marker}\n{meta}---\n\nBody written by {marker}.\n"


@pytest.fixture
async def org(pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


def _store(provider, org: str, user: str, *, scoped: bool = True, admin: bool = False) -> GraphSkillStore:
    return skill_store(provider, org, user, visibility_scope=user if scoped else None, is_admin=admin)


async def test_a_generic_id_is_accepted_and_a_second_create_is_refused(pg_provider, org, org_ctx) -> None:
    key = f"{org}_deploy"
    node = {
        "id": key, "orgId": org, "name": "deploy", "description": "d", "content": "c", "status": "active",
        "source": "manual", "version": "1.0.0", "createdBy": "user-a", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }
    with org_ctx(org):
        assert await pg_provider.create_node_if_absent(key, "agentSkills", dict(node)) is True
        assert await pg_provider.create_node_if_absent(key, "agentSkills", {**node, "createdBy": "user-b"}) is False
        assert (await pg_provider.get_document(key, "agentSkills"))["createdBy"] == "user-a"


async def test_a_second_creator_is_refused_and_the_first_skill_is_intact(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        owner = _store(pg_provider, org, "user-a")
        await owner.create_skill("deploy", _md("deploy", "owner-a"), resources={"notes.md": "a notes"})
        await owner.update_skill("deploy", _md("deploy", "owner-a-v2"))
        before = await pg_provider.get_document(f"{org}_deploy", "agentSkills")
        for scoped in (True, False):
            with pytest.raises(SkillAlreadyExistsError):
                await _store(pg_provider, org, "user-b", scoped=scoped).create_skill("deploy", _md("deploy", "other-b"))
        assert await pg_provider.get_document(f"{org}_deploy", "agentSkills") == before
        assert before["createdBy"] == "user-a"


async def test_n_parallel_creates_have_exactly_one_winner(pg_provider, org, org_ctx) -> None:
    rng = random.Random(SEED)
    users = [f"user-{i}" for i in range(rng.randint(6, 12))]
    name = f"race-{rng.randrange(1 << 20):05x}"

    async def one(user: str) -> str:
        with org_ctx(org):
            await _store(pg_provider, org, user, scoped=rng.random() < 0.5).create_skill(name, _md(name, f"marker-{user}"))
            return user

    results = await asyncio.gather(*(one(u) for u in users), return_exceptions=True)
    winners = [u for u, r in zip(users, results, strict=True) if not isinstance(r, BaseException)]
    assert len(winners) == 1, (SEED, results)
    assert all(isinstance(r, SkillAlreadyExistsError) for r in results if isinstance(r, BaseException)), (SEED, results)
    with org_ctx(org):
        doc = await pg_provider.get_document(f"{org}_{name}", "agentSkills")
    assert doc["createdBy"] == winners[0] and f"marker-{winners[0]}" in doc["content"], SEED


async def test_owner_lifecycle_and_moderation(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        owner = _store(pg_provider, org, "user-a")
        await owner.create_skill("life", _md("life", "v1"))
        await owner.update_skill("life", _md("life", "v2"))
        await owner.rollback("life", "1.0.0")
        assert await owner.write_resource("life", "extra.md", "more") is True
        assert await owner.set_skill_status("life", SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE) is True
        with pytest.raises(SkillPermissionError):
            await _store(pg_provider, org, "user-b", scoped=False).update_skill("life", _md("life", "changed"))
        admin = _store(pg_provider, org, "admin-1", scoped=False, admin=True)
        with pytest.raises(SkillPermissionError):
            await admin.update_skill("life", _md("life", "admin-edit"))
        assert await admin.delete_skill("life") is True
        assert await pg_provider.get_document(f"{org}_life", "agentSkills") is None


async def test_a_member_cannot_store_a_builtin_and_a_reused_name_starts_a_new_history(pg_provider, org, org_ctx) -> None:
    claim = _md("claimed", "member", extra="    source: builtin\n    pack_name: claimed\n    pack_version: 9.9.9\n")
    with org_ctx(org):
        old = _store(pg_provider, org, "user-a")
        with pytest.raises(RegistryError):
            await old.create_skill("claimed", claim)
        await old.create_skill("claimed", claim.replace("source: builtin", "source: manual"))
        doc = await pg_provider.get_document(f"{org}_claimed", "agentSkills")
        assert doc["source"] == "manual" and not doc.get("packName") and not doc.get("packVersion")

        await old.update_skill("claimed", _md("claimed", "old-owner-v2"))
        assert await old.delete_skill("claimed") is True
        new = _store(pg_provider, org, "user-b")
        await new.create_skill("claimed", _md("claimed", "new-owner"))
        assert await new.list_versions("claimed") == []
        with pytest.raises(RegistryError, match="not found"):
            await new.rollback("claimed", "1.0.0")
