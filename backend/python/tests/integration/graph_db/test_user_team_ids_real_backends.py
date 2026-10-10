"""``get_user_team_ids`` against a real Neo4j and a real ArangoDB.

Requires: your own throwaway Neo4j / ArangoDB and PCC_* env; see README.md in this folder.
Run: pytest tests/integration/graph_db/test_user_team_ids_real_backends.py -m integration
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import (
    collection_to_label,
    edge_collection_to_relationship,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import ArangoEnv, Neo4jEnv, unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "user_team_ids_it"

logger = logging.getLogger("user-team-ids-it")


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
    try:
        yield provider
    finally:
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


async def _seed_neo4j(provider: Neo4jProvider, tag: str, teams: list[tuple[str, str]]) -> str:
    user_label = collection_to_label(CollectionNames.USERS.value)
    team_label = collection_to_label(CollectionNames.TEAMS.value)
    rel = edge_collection_to_relationship(CollectionNames.PERMISSION.value)
    user_key = f"u-{tag}"
    await provider.client.execute_query(
        f"CREATE (:{user_label} {{id: $u, itTag: $tag}})", parameters={"u": user_key, "tag": tag}
    )
    for team_id, org in teams:
        await provider.client.execute_query(
            f"MATCH (u:{user_label} {{id: $u}}) "
            f"CREATE (u)-[:{rel} {{role: 'READER'}}]->(:{team_label} {{id: $t, orgId: $o, itTag: $tag}})",
            parameters={"u": user_key, "t": team_id, "o": org, "tag": tag},
        )
    return user_key


async def _clean_neo4j(provider: Neo4jProvider, tag: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.itTag = $tag DETACH DELETE n", parameters={"tag": tag}
    )


async def _seed_arango(provider: ArangoHTTPProvider, tag: str, teams: list[tuple[str, str]]) -> str:
    users, team_coll, perm = (
        CollectionNames.USERS.value, CollectionNames.TEAMS.value, CollectionNames.PERMISSION.value,
    )
    user_key = f"u-{tag}"
    await provider.http_client.execute_aql(
        f"INSERT {{_key: @u, email: @email}} INTO {users}", {"u": user_key, "email": f"{tag}@it.test"}
    )
    for team_id, org in teams:
        await provider.http_client.execute_aql(
            f"INSERT {{_key: @t, name: @t, orgId: @o, itTag: @tag}} INTO {team_coll}",
            {"t": team_id, "o": org, "tag": tag},
        )
        await provider.http_client.execute_aql(
            f"INSERT {{_from: @f, _to: @to, role: 'READER', itTag: @tag}} INTO {perm}",
            {"f": f"{users}/{user_key}", "to": f"{team_coll}/{team_id}", "tag": tag},
        )
    return user_key


async def _clean_arango(provider: ArangoHTTPProvider, tag: str) -> None:
    for coll in (CollectionNames.PERMISSION.value, CollectionNames.TEAMS.value):
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itTag == @tag REMOVE d IN {coll}", {"tag": tag}
        )
    users = CollectionNames.USERS.value
    await provider.http_client.execute_aql(
        f"FOR d IN {users} FILTER d.email == @email REMOVE d IN {users}", {"email": f"{tag}@it.test"}
    )


async def _check_org_scope_ids_only_and_truncation(provider, seed, clean, caplog) -> None:
    tag = uuid.uuid4().hex[:10]
    org_a, org_b = f"orgA-{tag}", f"orgB-{tag}"
    teams = [(f"t1-{tag}", org_a), (f"t2-{tag}", org_a), (f"t3-{tag}", org_a), (f"tx-{tag}", org_b)]
    try:
        user_key = await seed(provider, tag, teams)
        ids = await provider.get_user_team_ids(user_key, org_a)
        assert sorted(ids) == [f"t1-{tag}", f"t2-{tag}", f"t3-{tag}"]
        assert all(isinstance(i, str) for i in ids)
        assert await provider.get_user_team_ids(user_key, org_b) == [f"tx-{tag}"]
        assert await provider.get_user_team_ids(user_key, f"none-{tag}") == []

        with caplog.at_level(logging.WARNING, logger=logger.name):
            truncated = await provider.get_user_team_ids(user_key, org_a, limit=2)
        assert len(truncated) == 2
        assert any("truncated" in r.getMessage() for r in caplog.records)
    finally:
        await clean(provider, tag)


async def test_neo4j_user_team_ids_org_scoped_ids_only_and_truncation(neo4j, caplog) -> None:
    await _check_org_scope_ids_only_and_truncation(neo4j, _seed_neo4j, _clean_neo4j, caplog)


async def test_arango_user_team_ids_org_scoped_ids_only_and_truncation(arango, caplog) -> None:
    await _check_org_scope_ids_only_and_truncation(arango, _seed_arango, _clean_arango, caplog)
