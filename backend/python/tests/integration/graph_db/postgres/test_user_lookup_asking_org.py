"""``get_user_by_user_id`` bound to the asking organisation, on PostgreSQL, against a real database.

PostgreSQL binds every read to the ambient org already (``graph.user_by_user_id`` reads ``app.org_id``); an ``org_id``
argument names that org, an empty one matches no user, and another org is refused. Neo4j and ArangoDB:
``../test_user_lookup_asking_org_real_backends.py``.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import OrgMismatch

from .test_access import World

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[World]:
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        world = World(pg_provider, org)
        await world.nodes("users", {"_key": "k1", "userId": "uid-1", "email": "ann@example.com", "isActive": True})
        yield world
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_the_asking_org_finds_its_own_user(w: World, org_ctx) -> None:
    with org_ctx(w.org):
        assert (await w.p.get_user_by_user_id("uid-1", org_id=w.org))["_key"] == "k1"


async def test_without_an_org_the_lookup_is_unchanged(w: World, org_ctx) -> None:
    with org_ctx(w.org):
        assert (await w.p.get_user_by_user_id("uid-1"))["_key"] == "k1"
        assert await w.p.get_user_by_user_id("nope") is None


async def test_an_empty_org_matches_no_user(w: World, org_ctx) -> None:
    with org_ctx(w.org):
        assert await w.p.get_user_by_user_id("uid-1", org_id="") is None


async def test_another_org_is_refused(w: World) -> None:
    # The fixture holds the asking org's context for the test, so another org cannot be bound inside it.
    with pytest.raises(OrgMismatch):
        await w.p.get_user_by_user_id("uid-1", org_id=uuid.uuid4().hex[:24])
