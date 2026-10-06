"""Project -> hidden KB reconcile on a real Neo4j and a real ArangoDB.

Environment: PCC_NEO4J_URI, PCC_NEO4J_PASSWORD, PCC_ARANGO_URL, PCC_ARANGO_PASSWORD
(see tests/integration/graph_db/README.md); PCC_GATE=1 turns a missing backend into a failure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.handlers.project_kb_reconcile import (
    ProjectKbDesired,
    TeamEdgeRole,
    reconcile_project_kb,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import arango_env as load_arango_env
from ._backends import neo4j_env as load_neo4j_env
from ._backends import unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "project_kb_reconcile_it"
logger = logging.getLogger("project-kb-reconcile-it")


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, str]]:
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    if request.param == "neo4j":
        env = load_neo4j_env()
        monkeypatch.setenv("NEO4J_URI", env.uri)
        monkeypatch.setenv("NEO4J_USERNAME", env.user)
        monkeypatch.setenv("NEO4J_PASSWORD", env.password)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider = Neo4jProvider(logger, MagicMock())
        try:
            if not await asyncio.wait_for(provider.connect(), timeout=60):
                raise ConnectionError("connect returned False")
        except Exception as exc:
            unavailable(f"Neo4j not available at {env.uri}: {exc}")
        await provider.ensure_schema()
        try:
            yield provider, org_id
        finally:
            await provider.client.execute_query(
                "MATCH (n) WHERE n.orgId STARTS WITH $org DETACH DELETE n", parameters={"org": org_id}
            )
            await provider.disconnect()
        return

    env = load_arango_env()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": env.url, "username": env.user, "password": env.password, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        unavailable(f"ArangoDB not available at {env.url}: {exc}")
    try:
        yield provider, org_id
    finally:
        await provider.http_client.execute_aql(
            "FOR e IN permission FILTER CONTAINS(e._from, @k) OR CONTAINS(e._to, @k) REMOVE e IN permission",
            {"k": org_id},
        )
        for collection in (CollectionNames.USERS.value, CollectionNames.TEAMS.value, CollectionNames.APPS.value):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER STARTS_WITH(d.orgId, @org) REMOVE d IN {collection}",
                {"org": org_id},
            )


class Seed:
    def __init__(self, provider, org_id: str) -> None:
        self.p = provider
        self.org = org_id
        self.project_id = "proj1"
        self.kb = f"{org_id}-kb"
        self.all_team = f"all_{org_id}"

    def key(self, name: str) -> str:
        return f"{self.org}-{name}"

    def mongo(self, name: str) -> str:
        return f"mongo-{self.org}-{name}"

    async def user(self, name: str) -> str:
        key = self.key(name)
        await self.p.batch_upsert_nodes(
            [{"id": key, "userId": self.mongo(name), "orgId": self.org, "email": f"{key}@example.com", "isActive": True}],
            CollectionNames.USERS.value,
        )
        return key

    async def team(self, key: str) -> str:
        await self.p.batch_upsert_nodes([{"id": key, "name": key, "orgId": self.org}], CollectionNames.TEAMS.value)
        return key

    async def hidden_kb(self, created_by: str) -> None:
        await self.p.batch_upsert_nodes(
            [{
                "id": self.kb, "name": f"project:{self.project_id}", "orgId": self.org, "type": "KB",
                "appGroup": "Knowledge Base", "scope": "personal", "isActive": True, "isHidden": True,
                "createdBy": created_by, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }],
            CollectionNames.APPS.value,
        )

    async def edge(self, principal: str, collection: str, kind: str, role: str | None) -> None:
        edge = {
            "from_id": principal, "from_collection": collection, "to_id": self.kb,
            "to_collection": CollectionNames.APPS.value, "type": kind,
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }
        if role is not None:
            edge["role"] = role
        await self.p.batch_create_edges([edge], CollectionNames.PERMISSION.value)

    async def grant_user(self, key: str, role: str) -> None:
        await self.edge(key, CollectionNames.USERS.value, "USER", role)

    async def grant_team(self, key: str, role: str | None) -> None:
        await self.edge(key, CollectionNames.TEAMS.value, "TEAM", role)

    async def state(self) -> tuple[dict[str, str | None], str | None]:
        rows = await self.p.list_kb_permissions(self.kb)
        kb = await self.p.get_document(self.kb, CollectionNames.APPS.value)
        return {f"{r['type']}:{r['id']}": r["role"] for r in rows}, kb["createdBy"]


@pytest.fixture
async def seed(backend) -> Seed:
    provider, org_id = backend
    return Seed(provider, org_id)


def desired_for(seed: Seed, **overrides) -> ProjectKbDesired:
    fields = {
        "project_id": seed.project_id,
        "owner_user_id": seed.mongo("owner"),
        "editor_user_ids": [seed.mongo("editor")],
        "viewer_user_ids": [seed.mongo("viewer")],
        "teams": [TeamEdgeRole(team_id=seed.key("team"), role="WRITER")],
        "org_visible": True,
    }
    return ProjectKbDesired(**{**fields, **overrides})


async def seed_drifted_kb(seed: Seed) -> None:
    for name in ("owner", "editor", "viewer", "removed"):
        await seed.user(name)
    await seed.team(seed.key("team"))
    await seed.team(seed.key("stale-team"))
    await seed.team(seed.all_team)
    await seed.hidden_kb(created_by=seed.mongo("editor"))
    await seed.grant_user(seed.key("editor"), "OWNER")
    await seed.grant_user(seed.key("removed"), "READER")
    await seed.grant_team(seed.key("team"), None)
    await seed.grant_team(seed.key("stale-team"), "READER")


def expected_after(seed: Seed) -> dict[str, str | None]:
    return {
        f"USER:{seed.key('owner')}": "OWNER",
        f"USER:{seed.key('editor')}": "WRITER",
        f"USER:{seed.key('viewer')}": "READER",
        f"TEAM:{seed.key('team')}": "WRITER",
        f"TEAM:{seed.all_team}": "READER",
    }


async def test_converges_from_drift_and_a_second_run_changes_nothing(seed: Seed) -> None:
    await seed_drifted_kb(seed)

    first = await reconcile_project_kb(seed.p, seed.org, seed.kb, desired_for(seed))
    assert first.skipped_reason is None
    assert not first.ops.is_noop
    assert await seed.state() == (expected_after(seed), seed.mongo("owner"))
    assert await seed.p.count_kb_owners(seed.kb) == 1

    second = await reconcile_project_kb(seed.p, seed.org, seed.kb, desired_for(seed))
    assert second.ops.is_noop
    assert await seed.state() == (expected_after(seed), seed.mongo("owner"))


async def test_converges_again_after_the_graph_drifts_back(seed: Seed) -> None:
    await seed_drifted_kb(seed)
    await reconcile_project_kb(seed.p, seed.org, seed.kb, desired_for(seed))

    await seed.grant_user(seed.key("removed"), "WRITER")
    await seed.p.delete_edge(
        seed.all_team, CollectionNames.TEAMS.value, seed.kb, CollectionNames.APPS.value, CollectionNames.PERMISSION.value
    )

    repaired = await reconcile_project_kb(seed.p, seed.org, seed.kb, desired_for(seed))
    assert {g.principal_id for g in repaired.ops.grants} == {seed.all_team}
    assert {r.principal_id for r in repaired.ops.removals} == {seed.key("removed")}
    assert await seed.state() == (expected_after(seed), seed.mongo("owner"))


async def test_revoking_a_member_whose_edge_is_already_gone_is_not_an_error(seed: Seed) -> None:
    await seed_drifted_kb(seed)
    desired = desired_for(seed, editor_user_ids=[], viewer_user_ids=[], teams=[], org_visible=False)
    await reconcile_project_kb(seed.p, seed.org, seed.kb, desired)

    # The edge of a member removed while the event was in flight: nothing left to delete.
    result = await reconcile_project_kb(seed.p, seed.org, seed.kb, desired)
    assert result.ops.is_noop
    assert await seed.state() == ({f"USER:{seed.key('owner')}": "OWNER"}, seed.mongo("owner"))


async def test_deleting_a_missing_edge_directly_reports_false_without_raising(seed: Seed) -> None:
    await seed.user("ghost")
    await seed.hidden_kb(created_by=seed.mongo("ghost"))
    deleted = await seed.p.delete_edge(
        seed.key("ghost"), CollectionNames.USERS.value, seed.kb, CollectionNames.APPS.value, CollectionNames.PERMISSION.value
    )
    assert deleted is False


async def test_refuses_a_kb_that_is_not_the_projects_hidden_kb(seed: Seed) -> None:
    await seed_drifted_kb(seed)
    await seed.p.update_node(seed.kb, CollectionNames.APPS.value, {"isHidden": False})
    before = await seed.state()

    result = await reconcile_project_kb(seed.p, seed.org, seed.kb, desired_for(seed))

    assert result.skipped_reason
    assert await seed.state() == before


async def test_owner_handover_keeps_an_owner_throughout(seed: Seed) -> None:
    await seed.user("old-owner")
    await seed.user("owner")
    await seed.hidden_kb(created_by=seed.mongo("old-owner"))
    await seed.grant_user(seed.key("old-owner"), "OWNER")

    desired = desired_for(seed, editor_user_ids=[], viewer_user_ids=[], teams=[], org_visible=False)
    await reconcile_project_kb(seed.p, seed.org, seed.kb, desired)

    assert await seed.state() == ({f"USER:{seed.key('owner')}": "OWNER"}, seed.mongo("owner"))
