"""``get_team_with_users`` org scoping against a real Neo4j and a real ArangoDB.

Needs your own graph services; see README.md in this folder. Skips when PCC_* is unset, fails when PCC_GATE=1.

  cd backend/python && pytest tests/integration/graph_db/test_team_org_scope_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from ._backends import ArangoEnv, Neo4jEnv, unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "team_org_scope_it"

logger = logging.getLogger("team-org-scope-it")

TEAMS = CollectionNames.TEAMS.value
USERS = CollectionNames.USERS.value
PERMISSION = CollectionNames.PERMISSION.value


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch, neo4j_env: Neo4jEnv) -> AsyncIterator[Neo4jProvider]:
    monkeypatch.setenv("NEO4J_URI", neo4j_env.uri)
    monkeypatch.setenv("NEO4J_USERNAME", neo4j_env.user)
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_env.password)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
    except Exception as exc:
        unavailable(f"Neo4j not available at {neo4j_env.uri}: {exc}")
    await provider.ensure_schema()
    yield provider
    await provider.disconnect()


@pytest.fixture
async def arango(arango_env: ArangoEnv) -> AsyncIterator[ArangoHTTPProvider]:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": arango_env.url, "username": arango_env.user, "password": arango_env.password, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        unavailable(f"ArangoDB not available at {arango_env.url}: {exc}")
    yield provider


async def _seed_team(
    provider: Neo4jProvider | ArangoHTTPProvider,
    org_id: str,
    team_id: str,
    user_ids: list[str],
    *,
    team_org_id: str | None = None,
    omit_org_id: bool = False,
    created_by: str = "system",
) -> None:
    ts = get_epoch_timestamp_in_ms()
    team = {
        "id": team_id, "name": "Team", "createdBy": created_by,
        "createdAtTimestamp": ts, "updatedAtTimestamp": ts,
    }
    if not omit_org_id:
        team["orgId"] = org_id if team_org_id is None else team_org_id
    await provider.batch_upsert_nodes([team], TEAMS)
    await provider.batch_upsert_nodes([
        {"id": uid, "userId": uid, "orgId": org_id, "email": f"{uid}@example.com",
         "fullName": uid, "isActive": True, "createdAtTimestamp": ts, "updatedAtTimestamp": ts}
        for uid in user_ids
    ], USERS)
    await provider.batch_create_edges([
        {"from_id": uid, "from_collection": USERS, "to_id": team_id, "to_collection": TEAMS,
         "type": "USER", "role": "OWNER" if i == 0 else "READER",
         "createdAtTimestamp": ts, "updatedAtTimestamp": ts}
        for i, uid in enumerate(user_ids)
    ], PERMISSION)


async def _cleanup_neo4j(provider: Neo4jProvider, org_id: str, team_ids: list[str] | None = None) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.orgId = $org OR n.id IN $ids DETACH DELETE n",
        parameters={"org": org_id, "ids": team_ids or []},
    )


async def _cleanup_arango(
    provider: ArangoHTTPProvider, org_id: str, user_ids: list[str], team_ids: list[str] | None = None,
) -> None:
    if team_ids:
        await provider.http_client.execute_aql(
            f"FOR d IN {TEAMS} FILTER d._key IN @ids REMOVE d IN {TEAMS}", {"ids": team_ids},
        )
    await provider.http_client.execute_aql(
        f"FOR e IN {PERMISSION} FILTER e._from IN @users REMOVE e IN {PERMISSION}",
        {"users": [f"{USERS}/{u}" for u in user_ids]},
    )
    for collection in (TEAMS, USERS):
        await provider.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id},
        )


class TestTeamLookupIsOrgScoped:
    async def _assert_scoped(self, provider: Neo4jProvider | ArangoHTTPProvider, org_a: str, org_b: str, team_id: str, users: list[str]) -> None:
        assert await provider.get_team_with_users(team_id, users[0], org_a) is None

        team = await provider.get_team_with_users(team_id, users[0], org_b)
        assert team is not None
        assert team["id"] == team_id
        assert team["orgId"] == org_b
        assert sorted(m["userId"] for m in team["members"]) == sorted(users)
        assert team["canEdit"] is True

    async def test_neo4j(self, neo4j: Neo4jProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org_a, org_b = f"org-a-{suffix}", f"org-b-{suffix}"
        team_id = f"all_{org_b}"
        users = [f"u1-{suffix}", f"u2-{suffix}"]
        try:
            await _seed_team(neo4j, org_b, team_id, users)
            await self._assert_scoped(neo4j, org_a, org_b, team_id, users)
        finally:
            await _cleanup_neo4j(neo4j, org_b)

    async def test_arango(self, arango: ArangoHTTPProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org_a, org_b = f"org-a-{suffix}", f"org-b-{suffix}"
        team_id = f"all_{org_b}"
        users = [f"u1-{suffix}", f"u2-{suffix}"]
        try:
            await _seed_team(arango, org_b, team_id, users)
            await self._assert_scoped(arango, org_a, org_b, team_id, users)
        finally:
            await _cleanup_arango(arango, org_b, users)


async def _org_id_of(provider: Neo4jProvider | ArangoHTTPProvider, team_id: str) -> str | None:
    doc = await provider.get_document(team_id, TEAMS)
    return doc.get("orgId") if doc else None


class TestLegacyAllTeamWithoutOrgId:
    """S1-T1: a pre-orgId All team must not turn the next joiner into an OWNER."""

    async def _assert_heals(self, provider: Neo4jProvider | ArangoHTTPProvider, org: str, team_id: str, owner: str, joiner: str) -> None:
        async def roles() -> dict[str, str]:
            team = await provider.get_team_with_users(team_id, owner, org)
            assert team is not None
            return {m["userId"]: m["role"] for m in team["members"]}

        await provider.add_user_to_all_team(org, joiner)
        assert await _org_id_of(provider, team_id) == org
        assert await roles() == {owner: "OWNER", joiner: "READER"}

        await provider.ensure_all_team_with_users(org)
        assert list((await roles()).values()).count("OWNER") == 1

    async def test_neo4j(self, neo4j: Neo4jProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org, owner, joiner = f"org-{suffix}", f"own-{suffix}", f"join-{suffix}"
        team_id = f"all_{org}"
        try:
            await _seed_team(neo4j, org, team_id, [owner], omit_org_id=True)
            await _seed_team(neo4j, org, f"unused-{suffix}", [joiner])
            assert await _org_id_of(neo4j, team_id) is None
            await self._assert_heals(neo4j, org, team_id, owner, joiner)
        finally:
            await _cleanup_neo4j(neo4j, org, [team_id])

    async def test_arango(self, arango: ArangoHTTPProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org, owner, joiner = f"org-{suffix}", f"own-{suffix}", f"join-{suffix}"
        team_id = f"all_{org}"
        try:
            await _seed_team(arango, org, team_id, [owner], omit_org_id=True)
            await _seed_team(arango, org, f"unused-{suffix}", [joiner])
            assert await _org_id_of(arango, team_id) is None
            await self._assert_heals(arango, org, team_id, owner, joiner)
        finally:
            await _cleanup_arango(arango, org, [owner, joiner], [team_id])


class TestBackfillTeamOrgIds:
    async def _run(self, provider: Neo4jProvider | ArangoHTTPProvider, org: str, other_org: str, suffix: str) -> None:
        all_team, creator_team, orphan, stamped = f"all_{org}", f"t-c-{suffix}", f"t-o-{suffix}", f"t-s-{suffix}"
        creator = f"cr-{suffix}"
        await _seed_team(provider, org, all_team, [creator], omit_org_id=True)
        await _seed_team(provider, org, creator_team, [], omit_org_id=True, created_by=creator)
        await _seed_team(provider, org, orphan, [], omit_org_id=True, created_by="system")
        await _seed_team(provider, org, stamped, [], team_org_id=other_org, created_by=creator)

        first = await provider.backfill_team_org_ids()

        assert await _org_id_of(provider, all_team) == org
        assert await _org_id_of(provider, creator_team) == org
        assert await _org_id_of(provider, orphan) is None
        assert await _org_id_of(provider, stamped) == other_org
        assert first["updated"] >= 2
        assert orphan in first["unresolved_team_ids"]
        assert all_team not in first["unresolved_team_ids"]

        second = await provider.backfill_team_org_ids()
        assert second["updated"] == 0
        assert orphan in second["unresolved_team_ids"]

    async def test_neo4j(self, neo4j: Neo4jProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org, other = f"org-{suffix}", f"other-{suffix}"
        ids = [f"all_{org}", f"t-c-{suffix}", f"t-o-{suffix}", f"t-s-{suffix}"]
        try:
            await self._run(neo4j, org, other, suffix)
        finally:
            await _cleanup_neo4j(neo4j, org, ids)

    async def test_arango(self, arango: ArangoHTTPProvider) -> None:
        suffix = uuid.uuid4().hex[:10]
        org, other = f"org-{suffix}", f"other-{suffix}"
        ids = [f"all_{org}", f"t-c-{suffix}", f"t-o-{suffix}", f"t-s-{suffix}"]
        try:
            await self._run(arango, org, other, suffix)
        finally:
            await _cleanup_arango(arango, org, [f"cr-{suffix}"], ids)
