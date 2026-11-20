"""Box accounts that leave Box, on a real Neo4j and ArangoDB: PipesHub must let each person read what Box does.

The Box connector, its data source, the Box SDK and the production processor run for real over a real graph; only
Box's HTTP API is faked (the in-memory enterprise of the Box behaviour tests). Box documents the user statuses
``active``, ``inactive``, ``cannot_delete_edit`` and ``cannot_delete_edit_upload``
(https://developer.box.com/reference/resources/user/); an Inactive user "is prevented from logging in"
(https://docs.box.com/en/box-admin-tools/managing-box-users-and-admins/understanding-user-management/users-groups-settings),
and a ``company`` shared link "only provides access to this item to people the same company"
(https://developer.box.com/reference/put-files-id--add-shared-link/). So an account that cannot sign in, or that Box
no longer lists, reads nothing: not what was shared with it, and not what is open to the company.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests
from box_sdk_gen.networking import box_network_client, network

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.box.connector import BoxConnector
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.unit.connectors.sources.box.behaviour.box_behaviour_fakes import (
    FakeBoxApi,
    FakeConfigService,
    ccg_config,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("it.box.departed_accounts")
ARANGO_DB = "box_departed_accounts_it"
PEOPLE = ("alice", "bob", "carol", "dan")


@dataclass
class _Env:
    graph: IGraphDBProvider
    api: FakeBoxApi
    org_id: str
    connector_id: str
    processor: DataSourceEntitiesProcessor
    user_keys: dict[str, str] = field(default_factory=dict)
    connector: BoxConnector | None = None

    def email(self, name: str) -> str:
        return f"{name}@{self.org_id}.example"

    async def sync(self) -> None:
        """The first run is a full sync; later ones take the incremental path, as the stream position is fresh."""
        if self.connector is None:
            self.connector = BoxConnector(
                logger, self.processor, GraphDataStore(logger, self.graph),
                FakeConfigService(self.connector_id, ccg_config()), self.connector_id, "team", "it-admin",
            )
            assert await self.connector.init()
        await self.connector.run_sync()

    async def full_sync(self) -> None:
        """A full sync as the platform runs one: the saved positions are forgotten first."""
        await self.graph.delete_sync_points_by_connector_id(connector_id=self.connector_id)
        await self.sync()

    async def drive_grants(self, name: str) -> set[str]:
        """Record groups of this connector the person holds a grant on directly (their drive, "Shared with me")."""
        groups = await self.graph.get_nodes_by_filters(CollectionNames.RECORD_GROUPS.value, {"connectorId": self.connector_id})
        ids = {str(g.get("id") or g.get("_key")) for g in groups}
        edges = await self.graph.get_edges_from_node(f"users/{self.user_keys[name]}", CollectionNames.PERMISSION.value)
        return {e["_to"].split("/", 1)[1] for e in edges if e.get("_to", "").split("/", 1)[-1] in ids}

    async def readable(self, name: str) -> set[str]:
        """Names of the records the person may read, by the access check and by search."""
        docs = [d for d in await self.graph.get_nodes_by_filters(
            CollectionNames.RECORDS.value, {"connectorId": self.connector_id}) if not d.get("isDeleted")]
        by_id = {d.get("id") or d.get("_key"): d["recordName"] for d in docs}
        for record_id in by_id:
            await self.graph.update_node(record_id, CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "virtualRecordId": f"vr-{record_id}"})
        user = f"uid-{self.user_keys[name]}"  # the access reads take the user's userId, not the node id
        checked = set(await self.graph.filter_accessible_record_ids(list(by_id), user, self.org_id))
        searched = set((await self.graph.get_accessible_virtual_record_ids(user, self.org_id, raise_on_error=True)).values())
        assert checked == searched & set(by_id), f"the access check and search disagree for {name}"
        return {by_id[r] for r in checked}


async def _remove(graph: IGraphDBProvider, org_id: str, connector_id: str, user_keys: list[str]) -> None:
    with contextlib.suppress(Exception):
        await graph.delete_connector_instance(connector_id, org_id)
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, org_id, *user_keys]},
        )
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value,
                       CollectionNames.RECORD_GROUPS.value, CollectionNames.GROUPS.value):
        with contextlib.suppress(Exception):
            await graph.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d.connectorId == @c REMOVE d IN {collection}", {"c": connector_id})
    for collection, keys in ((CollectionNames.USERS.value, user_keys), (CollectionNames.APPS.value, [connector_id]),
                             (CollectionNames.ORGS.value, [org_id])):
        await graph.http_client.execute_aql(
            f"FOR k IN @keys REMOVE k IN {collection} OPTIONS {{ignoreErrors: true}}", {"keys": keys})


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    api = FakeBoxApi()
    session = requests.Session()
    session.mount("https://", api)
    session.mount("http://", api)
    monkeypatch.setattr(network, "BoxNetworkClient", lambda: box_network_client.BoxNetworkClient(requests_session=session))
    monkeypatch.setattr(box_network_client, "time", SimpleNamespace(sleep=lambda _s: None))
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-box-{run}", f"box-{run}"
        keys: list[str] = []
        cleanup.push_async_callback(_remove, graph, org_id, connector_id, keys)
        now = get_epoch_timestamp_in_ms()
        stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "name": "Acme", "accountType": "enterprise", "isActive": True, **stamps}],
            collection=CollectionNames.ORGS.value)
        assert await graph.batch_upsert_nodes(
            [{"id": connector_id, "orgId": org_id, "name": "Box", "type": "Box", "appGroup": "Box", "scope": "team",
              "isActive": True, "isConfigured": True, "isAuthenticated": True, "createdBy": "it-admin", **stamps}],
            collection=CollectionNames.APPS.value)
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages, **_: [True] * len(messages)
        e = _Env(graph, api, org_id, connector_id, processor)
        # Every person has a PipesHub account of the same email in the org, and a Box account.
        for name in PEOPLE:
            key = f"{run}-{name}"
            keys.append(key)
            e.user_keys[name] = key
            assert await graph.batch_upsert_nodes(
                [{"id": key, "userId": f"uid-{key}", "orgId": org_id, "email": e.email(name), "fullName": name,
                  "isActive": True, **stamps}], collection=CollectionNames.USERS.value)
            assert await graph.batch_create_edges(
                [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": org_id,
                  "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION", **stamps}],
                collection=CollectionNames.BELONGS_TO.value)
            api.add_user(f"u-{name}", e.email(name), name.title(), status="inactive" if name == "dan" else "active")
        api.add_file("file-shared", "plan.pdf", "u-alice")
        api.collaborate("file-shared", "u-carol")
        api.collaborate("file-shared", "u-bob")
        api.add_folder("fold-team", "Team", "u-alice")
        api.collaborate("fold-team", "u-carol")
        api.add_file("file-company", "handbook.pdf", "u-alice", shared_link_access="company")
        yield e


ALICE_READS = {"plan.pdf", "Team", "handbook.pdf"}
BOB_READS = {"plan.pdf", "handbook.pdf"}
CAROL_READS = {"plan.pdf", "Team", "handbook.pdf"}


async def test_an_inactive_account_reads_nothing_not_even_what_is_open_to_the_company(env: _Env) -> None:
    env.api.collaborate("file-shared", "u-dan")

    await env.sync()

    assert await env.readable("alice") >= ALICE_READS
    assert await env.readable("bob") == BOB_READS
    assert await env.readable("carol") == CAROL_READS
    assert await env.readable("dan") == set()


async def test_an_account_deactivated_then_reactivated_loses_then_regains_its_access(env: _Env) -> None:
    await env.sync()
    env.api.users["u-carol"]["status"] = "inactive"

    await env.sync()

    assert await env.readable("carol") == set()
    assert await env.readable("bob") == BOB_READS

    env.api.fail("GET", "/2.0/users", 503, times=10)
    await env.sync()  # a user list that cannot be read changes no one's access
    env.api.faults.clear()
    assert await env.readable("carol") == set()
    assert await env.readable("bob") == BOB_READS

    env.api.users["u-carol"]["status"] = "active"
    await env.sync()
    assert await env.readable("carol") == CAROL_READS


async def test_a_deleted_account_reads_nothing(env: _Env) -> None:
    await env.sync()
    del env.api.users["u-carol"]
    for collabs in env.api.collaborations.values():
        collabs[:] = [c for c in collabs if c["accessible_by"]["id"] != "u-carol"]

    await env.sync()

    assert await env.readable("carol") == set()
    assert await env.readable("bob") == BOB_READS


async def test_a_view_only_account_keeps_its_access(env: _Env) -> None:
    env.api.users["u-carol"]["status"] = "cannot_delete_edit_upload"

    await env.sync()

    assert await env.readable("carol") == CAROL_READS


async def test_the_only_account_that_can_sign_in_reads_nothing_once_deactivated(env: _Env) -> None:
    for name in ("bob", "carol"):
        env.api.users[f"u-{name}"]["status"] = "inactive"
    await env.sync()
    assert await env.readable("alice") >= ALICE_READS
    env.api.users["u-alice"]["status"] = "inactive"

    await env.sync()

    assert await env.readable("alice") == set()


async def test_a_full_sync_after_a_deactivation_gives_no_grant_back(env: _Env) -> None:
    await env.sync()
    assert await env.drive_grants("carol")
    env.api.users["u-carol"]["status"] = "inactive"
    await env.sync()

    await env.full_sync()

    assert await env.drive_grants("carol") == set()
    assert await env.readable("carol") == set()
    assert await env.readable("bob") == BOB_READS


async def test_a_returning_account_gets_back_a_share_only_the_share_history_names(env: _Env) -> None:
    """Erin has no PipesHub account, so her files are not walked: Carol's share of one is known from Box's
    collaboration history alone, which a returning account needs read again."""
    env.api.add_user("u-erin", f"erin@{env.org_id}.example", "Erin")
    env.api.add_folder("fold-erin", "Erin's", "u-erin")
    env.api.add_file("file-direct", "direct.pdf", "u-erin", parent="fold-erin")
    collab_id = env.api.collaborate("file-direct", "u-carol")
    env.api.add_event(
        "COLLABORATION_INVITE",
        {"type": "collaboration", "id": collab_id, "item": {"type": "file", "id": "file-direct"},
         "accessible_by": {"type": "user", "id": "u-carol", "login": env.email("carol")}},
        created_by={"type": "user", "id": "u-erin", "login": f"erin@{env.org_id}.example"},
        additional_details={"collab_id": collab_id},
    )
    await env.sync()
    assert "direct.pdf" in await env.readable("carol")
    env.api.users["u-carol"]["status"] = "inactive"
    await env.sync()
    assert await env.readable("carol") == set()
    env.api.users["u-carol"]["status"] = "active"

    await env.sync()

    assert await env.readable("carol") == CAROL_READS | {"direct.pdf"}


async def test_a_return_seen_on_a_user_list_cut_short_still_gets_its_full_sync(env: _Env) -> None:
    env.api.page_cap["/2.0/users"] = 3
    await env.sync()
    env.api.users["u-carol"]["status"] = "inactive"
    await env.sync()
    # The record of withdrawals is lost (the users sync point cleared), the stream position kept.
    await env.graph.delete_sync_points_by_connector_id(connector_id=env.connector_id)
    await env.connector.box_cursor_sync_point.update_sync_point(
        "event_stream_cursor", {"cursor": env.api.stream_head, "cursor_updated_at": get_epoch_timestamp_in_ms()})
    env.api.users["u-carol"]["status"] = "active"
    env.api.fail("GET", "/2.0/users", 503, times=10, query={"marker": "3"})  # carol is on the first page
    await env.sync()
    env.api.faults.clear()

    await env.sync()

    assert await env.readable("carol") == CAROL_READS
