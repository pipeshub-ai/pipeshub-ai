"""A knowledge base shared with a team is in its members' list, searched by name or not.

The Neo4j list query applied the name search to the knowledge base reached by
a direct grant on both of its branches, so on the team branch the condition
read a node that is null for a team-only knowledge base, and a member who
searched their list by name never found one their team gave them. ArangoDB
runs the same cases to keep both backends in step.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_list_team_access_it"

logger = logging.getLogger("kb-list-team-access-it")


@dataclass
class _Org:
    service: KnowledgeBaseService
    org_id: str
    member_id: str
    team_only: tuple[str, str]
    direct_only: tuple[str, str]
    both: tuple[str, str]


async def _remove(graph: IGraphDBProvider, ids: dict[str, list[str]]) -> None:
    for collection, keys in ids.items():
        if keys:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges(keys, collection)


def _user(key: str, user_id: str, org_id: str, run: str) -> dict:
    return {"_key": key, "userId": user_id, "orgId": org_id, "email": f"{user_id}-{run}@example.com",
            "isActive": True}


@pytest.fixture(params=["neo4j", "arango"])
async def org(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Org]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-kblist-{run}"
        owner_id, owner_key = f"owner-kblist-{run}", f"okey-kblist-{run}"
        member_id, member_key = f"member-kblist-{run}", f"mkey-kblist-{run}"
        team_key = f"team-kblist-{run}"
        seeded: dict[str, list[str]] = {
            CollectionNames.ORGS.value: [org_id],
            CollectionNames.USERS.value: [owner_key, member_key],
            CollectionNames.TEAMS.value: [team_key],
            CollectionNames.APPS.value: [],
        }
        cleanup.push_async_callback(_remove, graph, seeded)
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb list"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [_user(owner_key, owner_id, org_id, run), _user(member_key, member_id, org_id, run)],
            CollectionNames.USERS.value,
        )
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{"_key": team_key, "name": f"team {run}", "orgId": org_id, "createdBy": owner_key,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            CollectionNames.TEAMS.value,
        )
        # The edge PUT /api/v1/teams/:id writes for a member it adds (entity.py update_team).
        assert await graph.batch_create_edges(
            [{"from_id": member_key, "from_collection": CollectionNames.USERS.value,
              "to_id": team_key, "to_collection": CollectionNames.TEAMS.value,
              "type": "USER", "role": "READER", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            CollectionNames.PERMISSION.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id

        async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
            return processor

        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=processor_for_kb)

        kbs: dict[str, tuple[str, str]] = {}
        for label in ("team-only", "direct-only", "both"):
            name = f"kblist {label} {run}"
            created = await service.create_knowledge_base(user_id=owner_id, org_id=org_id, name=name)
            assert created and created.get("success") is not False, created
            seeded[CollectionNames.APPS.value].append(created["id"])
            kbs[label] = (created["id"], name)

        for label in ("team-only", "both"):
            granted = await service.create_kb_permissions(
                kbs[label][0], owner_id, user_ids=[], team_ids=[team_key], role="READER",
            )
            assert granted and granted.get("success") is not False, granted
        for label in ("direct-only", "both"):
            granted = await service.create_kb_permissions(
                kbs[label][0], owner_id, user_ids=[member_id], team_ids=[], role="READER",
            )
            assert granted and granted.get("success") is not False, granted

        yield _Org(service, org_id, member_id, kbs["team-only"], kbs["direct-only"], kbs["both"])


async def _listed(org: _Org, search: str | None) -> tuple[set[str], int]:
    result = await org.service.list_user_knowledge_bases(
        user_id=org.member_id, org_id=org.org_id, page=1, limit=50, search=search,
    )
    assert isinstance(result, dict) and "knowledgeBases" in result, result
    return {kb["id"] for kb in result["knowledgeBases"]}, result["pagination"]["totalCount"]


async def test_a_team_only_knowledge_base_is_found_by_its_name(org: _Org) -> None:
    kb_id, name = org.team_only

    listed, total = await _listed(org, name)

    assert listed == {kb_id}, f"searching {name!r} listed {listed}"
    assert total == 1


async def test_the_unfiltered_list_holds_every_grant_once(org: _Org) -> None:
    listed, total = await _listed(org, None)

    expected = {org.team_only[0], org.direct_only[0], org.both[0]}
    assert listed == expected
    assert total == len(expected)


async def test_a_name_search_keeps_only_the_matching_ones(org: _Org) -> None:
    listed, total = await _listed(org, "kblist team-only")

    assert listed == {org.team_only[0]}
    assert total == 1
