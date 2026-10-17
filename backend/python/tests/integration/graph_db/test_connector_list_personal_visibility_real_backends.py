"""Each user lists only their own personal connector, on real Neo4j and ArangoDB.

Two members and an admin in one org each own a personal connector, and the org
has one team connector. With no ``scope`` the list query used to return all
four to everyone, admins included, although opening another user's personal
connector answers 404. Each caller must see their own personal connector and
the team one, and the total must match the rows on every page.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope
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

ARANGO_DB = "connector_list_personal_visibility_it"
CALLERS = {"member_a": False, "member_b": False, "admin": True}

logger = logging.getLogger("connector-list-personal-visibility-it")


@dataclass
class _Org:
    graph: IGraphDBProvider
    org_id: str
    user_ids: dict[str, str]
    personal: dict[str, str]
    team: str

    async def page(self, caller: str, *, page: int = 1, limit: int = 20, **filters: object) -> tuple[list[str], int]:
        documents, total = await self.graph.get_filtered_connector_instances(
            collection=CollectionNames.APPS.value,
            edge_collection=CollectionNames.ORG_APP_RELATION.value,
            org_id=self.org_id,
            user_id=self.user_ids[caller],
            skip=(page - 1) * limit,
            limit=limit,
            is_admin=CALLERS[caller],
            **filters,
        )
        return [d.get("_key") or d.get("id") for d in documents], total


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
        org_id = f"org-plv-{run}"
        user_ids = {caller: f"{caller}-{run}" for caller in CALLERS}
        personal = {caller: f"personal-{caller}-{run}" for caller in CALLERS}
        team = f"team-{run}"
        now = get_epoch_timestamp_in_ms()

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges([*personal.values(), team], CollectionNames.APPS.value)
                await graph.delete_nodes_and_edges([org_id], CollectionNames.ORGS.value)

        cleanup.push_async_callback(remove)

        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "accountType": "enterprise", "name": "Acme", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.ORGS.value,
        )
        apps = [(personal[c], ConnectorScope.PERSONAL.value, user_ids[c]) for c in CALLERS]
        apps.append((team, ConnectorScope.TEAM.value, user_ids["admin"]))
        assert await graph.batch_upsert_nodes(
            [{"id": app_id, "name": app_id, "type": "Drive", "appGroup": "Google Workspace",
              "authType": "OAUTH", "scope": scope, "orgId": org_id, "isActive": True,
              "isAgentActive": True, "isConfigured": True, "isAuthenticated": True,
              "createdBy": creator, "updatedBy": creator,
              "createdAtTimestamp": now + i, "updatedAtTimestamp": now + i}
             for i, (app_id, scope, creator) in enumerate(apps)],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_create_edges(
            [{"from_id": org_id, "from_collection": CollectionNames.ORGS.value,
              "to_id": app_id, "to_collection": CollectionNames.APPS.value,
              "createdAtTimestamp": now}
             for app_id, _scope, _creator in apps],
            collection=CollectionNames.ORG_APP_RELATION.value,
        )
        yield _Org(graph, org_id, user_ids, personal, team)


@pytest.mark.parametrize("caller", list(CALLERS))
@pytest.mark.parametrize(
    "filters",
    [
        pytest.param({}, id="all"),
        pytest.param({"is_configured": True}, id="configured"),
        pytest.param({"is_configured": True, "is_agent_active": True}, id="agents-active"),
    ],
)
async def test_each_caller_lists_only_their_own_personal_connector(
    org: _Org, caller: str, filters: dict
) -> None:
    keys, total = await org.page(caller, **filters)

    assert set(keys) == {org.personal[caller], org.team}
    assert total == len(keys) == 2


@pytest.mark.parametrize("caller", list(CALLERS))
async def test_the_total_matches_the_rows_on_every_page(org: _Org, caller: str) -> None:
    first, first_total = await org.page(caller, page=1, limit=1)
    second, second_total = await org.page(caller, page=2, limit=1)
    third, third_total = await org.page(caller, page=3, limit=1)

    assert (first_total, second_total, third_total) == (2, 2, 2)
    assert len(first) == len(second) == 1
    assert third == []
    assert {*first, *second} == {org.personal[caller], org.team}


@pytest.mark.parametrize("caller", list(CALLERS))
async def test_the_personal_scope_still_lists_only_the_callers_own(org: _Org, caller: str) -> None:
    keys, total = await org.page(caller, scope=ConnectorScope.PERSONAL.value)

    assert keys == [org.personal[caller]]
    assert total == 1


@pytest.mark.parametrize("caller", list(CALLERS))
async def test_the_agent_and_chat_lookup_agrees_with_the_list(org: _Org, caller: str) -> None:
    documents = await org.graph.get_user_connector_instances(
        collection=CollectionNames.APPS.value,
        user_id=org.user_ids[caller],
        org_id=org.org_id,
        team_scope=ConnectorScope.TEAM.value,
        personal_scope=ConnectorScope.PERSONAL.value,
    )

    assert {d.get("_key") or d.get("id") for d in documents} == {org.personal[caller], org.team}
