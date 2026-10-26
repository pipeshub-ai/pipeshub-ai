"""Linear access on real Neo4j and ArangoDB: PipesHub must show each user what Linear does.

The Linear connector, its data source and GraphQL documents, the production processor
and the graph run for real; only Linear's servers are faked (``linear_workspace``). After
each sync every user's readable set is compared with Linear's own rules, for every record
the connector wrote (issues, attachments, documents, files), through both the batch access
check and search, which trusts a team declared ``RECORD_GROUP_LEVEL`` without a per-record
check. Each scenario is a way the connector has leaked or lost access before.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import types
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.sync_runner import (
    _begin_full_sync_run,
    _unlink_what_full_sync_missed,
)
from app.connectors.sources.linear import connector as connector_module
from app.connectors.sources.linear.connector import LinearConnector
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.sources.client.graphql.client import GraphQLClient
from app.sources.external.linear import linear as datasource_module
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.graph_db.linear_workspace import (
    Attachment,
    Comment,
    Document,
    Issue,
    LinearWorkspace,
    Link,
    Project,
    Team,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.models.entities import Record
    from app.models.permission import Permission
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.sources.client.graphql.response import GraphQLResponse

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "linear_access_it"
logger = logging.getLogger("linear-access-it")


class _Config:
    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.path, self.config = f"/services/connectors/{connector_id}/config", config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        return self.config if path == self.path else default


@dataclass
class _Env:
    graph: IGraphDBProvider
    ws: LinearWorkspace
    org_id: str
    connector_id: str
    processor: DataSourceEntitiesProcessor
    config: dict[str, Any]
    user_ids: dict[str, str]  # email -> PipesHub userId
    connector: LinearConnector | None = None

    # ------------------------------------------------------------------ sync

    async def _connector(self) -> LinearConnector:
        if self.connector is None:
            self.connector = LinearConnector(
                logger, self.processor, GraphDataStore(logger, self.graph), _Config(self.connector_id, self.config),
                self.connector_id, "team", "it-admin",
            )
            self.connector.notify = AsyncMock()
            assert await self.connector.init() is True
        return self.connector

    async def sync(self) -> None:
        connector = await self._connector()
        self.ws.tick()
        await connector.run_sync()
        self.ws.tick(5 * 60_000)  # syncs run minutes apart; the connector re-reads a short overlap

    async def full_sync(self, *, complete: bool = True) -> None:
        """A full sync as the platform runs one, which every filter change asks for: the
        checkpoints are forgotten, the connector syncs, then the platform unlinks every
        node that sync did not write. A sync that skipped part of the source
        (``complete=False``) unlinks nothing and owes the platform one more full sync."""
        connector = await self._connector()
        await self.graph.delete_sync_points_by_connector_id(connector_id=self.connector_id)
        processor, seen = _begin_full_sync_run(connector, self.connector_id)
        assert seen is not None
        try:
            await self.sync()
            owes_nothing = await _unlink_what_full_sync_missed(connector, self.connector_id, self.graph, logger, seen)
            assert owes_nothing is complete
        finally:
            processor.end_full_sync_run(self.connector_id, seen)

    def select_teams(self, *teams: Team) -> None:
        self.config["filters"]["sync"]["values"]["team_ids"] = {
            "operator": "in", "type": "list", "value": [t.id for t in teams]}

    # ------------------------------------------------------------------ reading the graph

    async def records(self) -> list[dict[str, Any]]:
        docs = await self.graph.get_nodes_by_filters(CollectionNames.RECORDS.value, {"connectorId": self.connector_id})
        return [{**d, "id": d.get("id") or d.get("_key")} for d in docs
                if not d.get("isPlaceholder") and not d.get("isDeleted")]

    async def ticket_names(self) -> set[str]:
        return {d["recordName"] for d in await self.records() if d["recordType"] == "TICKET"}

    async def record(self, external_id: str) -> dict[str, Any] | None:
        return next((d for d in await self.records() if d.get("externalRecordId") == external_id), None)

    async def stored_id(self, external_id: str) -> str | None:
        """The record's id, stubs included."""
        docs = await self.graph.get_nodes_by_filters(
            CollectionNames.RECORDS.value, {"connectorId": self.connector_id, "externalRecordId": external_id})
        return next((d.get("id") or d.get("_key") for d in docs or [] if not d.get("isDeleted")), None)

    async def readable(self, email: str) -> set[str]:
        """Record ids the user may read by the access check, by search, or by browsing."""
        return set().union(*await self.access(email))

    async def listed(self, email: str) -> set[str]:
        """Record ids the Knowledge Hub lists for the user (All Records, flattened)."""
        ids: set[str] = set()
        cursor = None
        for _ in range(50):
            page = await KnowledgeHubService(logger, self.graph).get_nodes(
                user_id=self.user_ids[email], org_id=self.org_id, flattened=True, origins=["CONNECTOR"],
                limit=200, cursor=cursor)
            assert page.success, page.error
            ids |= {item.id for item in page.items}
            cursor = page.pagination.nextCursor if page.pagination else None
            if not cursor:
                return ids
        raise AssertionError("the Knowledge Hub listing did not end")

    async def access(self, email: str) -> tuple[set[str], set[str], set[str]]:
        """Record ids the user may read by the access check, those search finds for them,
        and those the Knowledge Hub lists for them."""
        docs = await self.records()
        ids = [d["id"] for d in docs]
        checked = await self.graph.filter_accessible_record_ids(ids, self.user_ids[email], self.org_id)
        # Search returns indexed records only; stand in for the indexing service.
        for doc in docs:
            await self.graph.update_node(doc["id"], CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "virtualRecordId": doc.get("virtualRecordId") or f"vr-{doc['id']}"})
        searched = await self.graph.get_accessible_virtual_record_ids(
            self.user_ids[email], self.org_id, raise_on_error=True)
        return set(checked), set(searched.values()) & set(ids), await self.listed(email) & set(ids)

    # ------------------------------------------------------------------ the oracle

    def in_scope(self, item: Issue | Project) -> bool:
        """Whether the sync filters cover an issue or project. What they leave out may stay
        stored, but nobody may read it."""
        values = self.config["filters"]["sync"]["values"]
        team_filter = values.get("team_ids") or {}
        team_ids = set(team_filter.get("value") or [])
        if team_ids:
            teams = {item.team.id} if isinstance(item, Issue) else {t.id for t in item.teams}
            excluding = team_filter.get("operator") == "not_in"
            if (teams <= team_ids) if excluding else not (teams & team_ids):
                return False
        start = ((values.get("created") or {}).get("value") or {}).get("start")
        return not (start and item.created_at < start)

    def synced_team_ids(self) -> set[str]:
        """The teams the sync files records under: those the syncing account sees that the
        team filter keeps."""
        team_filter = self.config["filters"]["sync"]["values"].get("team_ids") or {}
        team_ids = set(team_filter.get("value") or [])
        excluding = team_filter.get("operator") == "not_in"
        return {t.id for t in self.ws.teams if self.ws.can_see_team(self.ws.sync_user, t)
                and (not team_ids or ((t.id not in team_ids) if excluding else (t.id in team_ids)))}

    def readers(self, item: Issue | Project) -> set[str]:
        """Emails of the users PipesHub shows an issue or project to. Linear shows a project
        in several teams to the readers of any of them; PipesHub, by choice, only to those of
        its home team: the lead team, else the first synced team by id."""
        if not isinstance(item, Project):
            return self.ws.readers(item)
        synced = self.synced_team_ids()
        teams = [t for t in item.teams if t.id in synced]
        home = item.lead if item.lead in teams else min(teams, key=lambda t: t.id, default=None)
        if item.trashed or home is None:
            return set()
        return {u.email for u in self.ws.users if self.ws.can_see_team(u, home)}

    def owners(self, doc: dict[str, Any]) -> list[Issue | Project]:
        """The Linear issues or projects a record's content comes from."""
        external_id = str(doc.get("externalRecordId") or "")
        for issue in self.ws.issues:
            if external_id == issue.id or any(external_id == c.id for c in [*issue.attachments, *issue.documents]):
                return [issue]
        for project in self.ws.projects:
            if external_id == project.id or any(external_id == c.id for c in [*project.links, *project.documents]):
                return [project]
        # A file is one record per issue or project that links it (``<url>#parent=<id>``).
        url, _, parent = external_id.partition("#parent=")
        if not url:
            return []
        linking = [i for i in self.ws.issues if url in " ".join([i.description, *(c.body for c in i.comments)])] + [
            p for p in self.ws.projects if url in p.content]
        return [o for o in linking if not parent or o.id == parent]

    async def assert_matches_linear(self) -> None:
        """Every user reads exactly what Linear shows them; nothing gone from Linear is kept."""
        docs = await self.records()
        problems: list[str] = []
        expected: dict[str, set[str]] = {}
        for doc in docs:
            owners = self.owners(doc)
            name = f"{doc.get('recordType')} {doc.get('recordName')}"
            if not owners or all(o.trashed for o in owners):
                problems.append(f"{name} is stored but gone from Linear")
            covered = [o for o in owners if self.in_scope(o)]
            if doc.get("recordType") == "FILE":
                # A file is one record per issue that links it, or one record for all of them.
                expected[doc["id"]] = set().union(*(self.readers(o) for o in covered)) if covered else set()
            else:
                expected[doc["id"]] = self.readers(owners[0]) if owners and owners[0] in covered else set()
        for email in self.user_ids:
            checked, searched, listed = await self.access(email)
            for doc in docs:
                name = f"{doc.get('recordType')} {doc.get('recordName')!r}"
                allowed = email in expected[doc["id"]]
                if doc["id"] in checked | searched | listed and not allowed:
                    problems.append(f"{email} can reach {name}, which Linear hides from them")
                elif allowed and doc["id"] not in checked:
                    problems.append(f"{email} cannot read {name}, which Linear shows them")
                elif allowed and doc["id"] not in searched:
                    problems.append(f"{email} cannot find {name} in search, which Linear shows them")
                elif allowed and doc["id"] not in listed:
                    problems.append(f"{email} cannot browse to {name}, which Linear shows them")
        assert not problems, "PipesHub disagrees with Linear:\n  " + "\n  ".join(sorted(problems))


async def _remove(graph: IGraphDBProvider, org_id: str, connector_id: str, user_keys: list[str]) -> None:
    with contextlib.suppress(Exception):
        await graph.delete_connector_instance(connector_id, org_id)
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, org_id, *user_keys]},
        )
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.TICKETS.value, CollectionNames.FILES.value,
                       CollectionNames.LINKS.value, CollectionNames.WEBPAGES.value,
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
    # A full sync owed by one that skipped part of the source is remembered per process.
    monkeypatch.setattr(sync_runner, "_INCOMPLETE_FULL_SYNC_RETRYING", set())
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        ws = LinearWorkspace(url_key=f"acme{uuid.uuid4().hex[:6]}")
        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-linear-{run}", f"linear-{run}"
        user_keys: list[str] = []
        cleanup.push_async_callback(_remove, graph, org_id, connector_id, user_keys)

        async def execute(
            client: GraphQLClient, query: str, variables: dict[str, Any] | None = None, operation_name: str | None = None,
        ) -> GraphQLResponse:
            assert client.endpoint == "https://api.linear.app/graphql", client.endpoint
            if ws.token not in str(client.headers.get("Authorization")):
                return ws.unauthenticated()
            return await ws.execute(query, variables)

        monkeypatch.setattr(GraphQLClient, "execute", execute)
        transport = ws.http()
        shim = types.SimpleNamespace(**{n: getattr(httpx, n) for n in dir(httpx) if not n.startswith("__")})
        shim.AsyncClient = lambda *a, **kw: httpx.AsyncClient(*a, **{**kw, "transport": transport})
        monkeypatch.setattr(datasource_module, "httpx", shim)
        # Retries wait seconds between attempts; the fake answers at once.
        aio = types.SimpleNamespace(**{n: getattr(asyncio, n) for n in dir(asyncio) if not n.startswith("__")})
        aio.sleep = AsyncMock()
        monkeypatch.setattr(connector_module, "asyncio", aio)
        monkeypatch.setattr(connector_module, "get_epoch_timestamp_in_ms", lambda: ws.now)

        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{"id": org_id, "name": "Acme", "accountType": "enterprise", "isActive": True,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.ORGS.value)
        assert await graph.batch_upsert_nodes(
            [{"id": connector_id, "orgId": org_id, "name": "Linear", "type": "Linear", "appGroup": "Linear",
              "scope": "team", "isActive": True, "isConfigured": True, "isAuthenticated": True,
              "authType": "API_TOKEN", "createdBy": "it-admin", "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=CollectionNames.APPS.value)

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages, **_: [True] * len(messages)
        config = {"auth": {"authType": "API_TOKEN", "apiToken": ws.token}, "credentials": {},
                  "filters": {"sync": {"values": {}}, "indexing": {"values": {}}}}
        environment = _Env(graph, ws, org_id, connector_id, processor, config, {})

        async def add_user(email: str) -> None:
            key, user_id = f"{run}-{uuid.uuid4().hex[:8]}", f"user-{uuid.uuid4().hex[:10]}"
            user_keys.append(key)
            assert await graph.batch_upsert_nodes(
                [{"id": key, "userId": user_id, "orgId": org_id, "email": email, "fullName": email, "isActive": True,
                  "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.USERS.value)
            assert await graph.batch_create_edges(
                [{"from_id": key, "from_collection": CollectionNames.USERS.value, "to_id": org_id,
                  "to_collection": CollectionNames.ORGS.value, "entityType": "ORGANIZATION",
                  "createdAtTimestamp": now, "updatedAtTimestamp": now}], collection=CollectionNames.BELONGS_TO.value)
            environment.user_ids[email] = user_id

        environment.add_user = add_user  # type: ignore[attr-defined]
        yield environment
        assert not ws.gaps, f"the connector asked for what the fake Linear does not model: {sorted(set(ws.gaps))}"


def _name(issue: Issue) -> str:
    """The record name the connector gives an issue."""
    return f"[{issue.identifier}] {issue.title}"


async def _people(env: _Env, *names: str, guests: tuple[str, ...] = (), suspended: tuple[str, ...] = ()) -> dict[str, Any]:
    """Linear users, each with a PipesHub account of the same email, plus a PipesHub user
    with no Linear account. The sync account is an admin member of every team it syncs."""
    people = {n: env.ws.user(n.title(), guest=n in guests, active=n not in suspended) for n in ("sync", *names)}
    env.ws.sync_user = people["sync"]
    for person in people.values():
        await env.add_user(person.email)  # type: ignore[attr-defined]
    await env.add_user(f"outsider@{env.ws.url_key}.example")  # type: ignore[attr-defined]
    return people


# ---------------------------------------------------------------------- the permission model

async def test_team_visibility_hierarchy_matches_linear(env: _Env) -> None:
    """Public, private and restricted teams, sub-teams, guests and suspended accounts."""
    p = await _people(env, "alice", "bob", "carol", "gail", "sam", "rita", guests=("gail",), suspended=("sam",))
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"], p["sam"]])
    portal = ws.team("PORTAL", members=[p["sync"], p["gail"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    secops = ws.team("SECOPS", visibility="restricted", parent=sec, members=[p["sync"], p["rita"]])
    eng_web = ws.team("ENGWEB", parent=eng, members=[p["sync"], p["carol"]])
    eng_hr = ws.team("ENGHR", visibility="private", parent=eng, members=[p["sync"], p["carol"]])
    for team in (eng, portal, sec, secops, eng_web, eng_hr):
        ws.issue(team, f"{team.key} roadmap")

    await env.sync()

    await env.assert_matches_linear()
    # Spot checks of the rules the oracle encodes, so a fake change cannot weaken them unseen.
    assert ws.readers(ws.issues[0]) >= {p["alice"].email, p["bob"].email, p["carol"].email}
    assert p["gail"].email not in ws.readers(ws.issues[0]), "guests see only their own teams"
    assert p["sam"].email not in ws.readers(ws.issues[0]), "suspended accounts see nothing"
    assert p["alice"].email in ws.readers(ws.issues[3]), "a restricted team is open to its parent's members"
    assert p["bob"].email not in ws.readers(ws.issues[5]), "a private sub-team is closed to its parent's members"


async def test_a_team_filter_keeps_the_hierarchy(env: _Env) -> None:
    """Syncing only a restricted sub-team still grants its private parent's members."""
    p = await _people(env, "alice", "rita", "bob")
    ws = env.ws
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    secops = ws.team("SECOPS", visibility="restricted", parent=sec, members=[p["sync"], p["rita"]])
    ws.team("ENG", members=[p["sync"], p["bob"]])
    ws.issue(sec, "Pen test scope")
    rotate = ws.issue(secops, "Rotate the on-call keys")
    env.select_teams(secops)

    await env.sync()

    assert await env.ticket_names() == {_name(rotate)}
    await env.assert_matches_linear()


async def test_a_sub_team_synced_without_its_parent_stays_readable(env: _Env) -> None:
    """The filter selects sub-teams only. Their groups hang under parents the sync does
    not cover, which must not be cleaned away as deselected on a later sync."""
    p = await _people(env, "alice", "bob", "carol")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    web = ws.team("WEB", visibility="private", parent=eng, members=[p["sync"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    ops = ws.team("OPS", visibility="restricted", parent=sec, members=[p["sync"], p["carol"]])
    ws.issue(eng, "Not synced")
    ws.issue(web, "Landing page")
    ws.issue(ops, "Rotate keys")
    env.select_teams(web, ops)

    for _ in range(3):
        await env.sync()
        assert await env.ticket_names() == {_name(ws.issues[1]), _name(ws.issues[2])}
        await env.assert_matches_linear()


async def test_a_parent_team_pass_leaves_its_sub_teams_out(env: _Env) -> None:
    """Linear's team filter also returns the sub-teams' issues. Syncing the parent alone
    must not pull in a private sub-team the filter left out."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    sal = ws.team("SAL", visibility="private", members=[p["sync"], p["alice"], p["bob"]])
    mar = ws.team("MAR", visibility="private", parent=sal, members=[p["sync"], p["alice"]])
    deal = ws.issue(sal, "Renewal pricing")
    ws.issue(mar, "Campaign budget", attachments=[Attachment("Plan", "https://docs.example/plan")])
    env.select_teams(sal)

    for _ in range(2):
        await env.sync()
        assert await env.ticket_names() == {_name(deal)}
        assert {d["recordType"] for d in await env.records()} == {"TICKET"}, "the sub-team's attachment was synced"
        await env.assert_matches_linear()


async def test_narrowing_and_widening_the_team_filter(env: _Env) -> None:
    """A filter change runs a full sync. What the narrower filter leaves out stays stored
    and readable by nobody; once the filter widens it is readable again, as the same record."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    build = ws.issue(eng, "Build pipeline", attachments=[Attachment("CI", "https://ci.example/1")])
    audit = ws.issue(sec, "Access review")
    await env.sync()
    assert await env.ticket_names() == {_name(build), _name(audit)}
    kept = {x.id: (await env.record(x.id))["id"] for x in (build, build.attachments[0])}

    env.select_teams(sec)
    await env.full_sync()
    assert {x: (await env.record(x) or {}).get("id") for x in kept} == kept, "a deselected team's record was deleted"
    for email in env.user_ids:
        assert not set(kept.values()) & await env.readable(email), f"{email} still reads the deselected team"
    await env.assert_matches_linear()

    env.config["filters"]["sync"]["values"] = {}
    await env.full_sync()
    assert {x: (await env.record(x) or {}).get("id") for x in kept} == kept, "the records were created again"
    assert set(kept.values()) <= await env.readable(p["bob"].email)
    await env.assert_matches_linear()


async def test_narrowing_the_created_date_filter_keeps_older_issues_unreadable(env: _Env) -> None:
    """Issues created before a newly set date stay stored, and nobody can read them. An old
    parent of a newer sub-issue stays as a stub, so the sub-issue keeps its place and readers."""
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    old = ws.issue(eng, "Old task", attachments=[Attachment("Spec", "https://docs.example/spec")])
    await env.sync()
    cut = ws.tick()
    # Written while its parent is stored, so it hangs under the parent, which falls outside the new range
    new = ws.issue(eng, "New task", parent=old)
    await env.sync()
    old_ids = {await env.stored_id(x.id) for x in (old, old.attachments[0])}

    env.config["filters"]["sync"]["values"]["created"] = {
        "operator": "is_after", "type": "datetime", "value": {"start": cut, "end": None}}
    await env.full_sync()

    assert {await env.stored_id(x.id) for x in (old, old.attachments[0])} == old_ids, (
        "an issue the date filter leaves out was deleted")
    assert await env.record(old.id) is None, "the old parent is not a stub"
    assert not old_ids & await env.readable(p["alice"].email)
    assert (await env.record(new.id))["id"] in await env.readable(p["alice"].email)
    await env.assert_matches_linear()

    # A new comment brings an issue back for re-reading; one the filter leaves out stays out.
    now = ws.tick()
    old.comments.append(Comment("Still relevant?", p["alice"], created_at=now, updated_at=now))
    await env.sync()
    assert not old_ids & await env.readable(p["alice"].email)
    await env.assert_matches_linear()


async def test_a_full_sync_that_fails_partway_takes_no_access_away(env: _Env) -> None:
    """A team whose issues could not be read is not counted as read: the platform must
    not unlink what that sync did not get to write."""
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    for n in range(120):
        ws.issue(eng, f"Issue {n:03d}")
    await env.sync()
    await env.assert_matches_linear()

    ws.failing["issues"] = lambda variables: bool(variables.get("after"))  # every later page, every retry
    await env.full_sync(complete=False)
    del ws.failing["issues"]

    await env.assert_matches_linear()


async def test_a_created_date_filter_keeps_access_exact(env: _Env) -> None:
    """Sub-issues created after the cut-off are synced; their older parents are not."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    old_epic = ws.issue(sec, "Old epic")
    ws.issue(eng, "Old task")
    cut = ws.tick()
    new_child = ws.issue(sec, "New sub-task", parent=old_epic)
    new_task = ws.issue(eng, "New task")
    env.config["filters"]["sync"]["values"]["created"] = {
        "operator": "is_after", "type": "datetime", "value": {"start": cut, "end": None}}

    for _ in range(2):
        await env.sync()
        assert await env.ticket_names() == {_name(new_child), _name(new_task)}
        await env.assert_matches_linear()


async def test_turning_off_issue_indexing_keeps_the_records_readable(env: _Env) -> None:
    p = await _people(env, "alice")
    ws = env.ws
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    ws.issue(sec, "Pen test", attachments=[Attachment("Scope", "https://docs.example/scope")])
    env.config["filters"]["indexing"]["values"]["issues"] = {"operator": "is", "type": "boolean", "value": False}

    await env.sync()

    statuses = {d["recordType"]: d.get("indexingStatus") for d in await env.records()}
    assert statuses["TICKET"] == "AUTO_INDEX_OFF", statuses
    assert statuses["LINK"] != "AUTO_INDEX_OFF", "turning issues off also turned off their attachments"
    await env.assert_matches_linear()


async def test_membership_and_visibility_changes_reach_access(env: _Env) -> None:
    p = await _people(env, "alice", "bob")
    ws = env.ws
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"], p["bob"]])
    ws.issue(sec, "Incident review")
    await env.sync()
    await env.assert_matches_linear()

    sec.members.remove(p["bob"])
    await env.sync()
    await env.assert_matches_linear()

    sec.visibility = "public"
    await env.sync()
    await env.assert_matches_linear()

    p["alice"].active = False  # suspended in Linear
    await env.sync()
    await env.assert_matches_linear()


# ---------------------------------------------------------------------- projects in several teams

async def test_a_project_is_readable_through_its_home_team_only(env: _Env) -> None:
    """A project in several teams, with its documents, links and files, is filed under its
    lead team, else its first team by id, whatever order the teams are listed in."""
    p = await _people(env, "alice", "bob", "carol", "gail", guests=("gail",))
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["carol"], p["gail"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    legal = ws.team("LEGAL", visibility="private", members=[p["sync"], p["bob"]])
    plan = ws.upload("plan.pdf", b"%PDF plan")
    for name, teams, lead in (("Launch", [sec, eng], None), ("Launch again", [eng, sec], None),
                              ("Audit", [legal, sec], None), ("Review", [sec, legal], legal)):
        ws.project(name, teams, lead=lead, content=f"[plan.pdf]({plan})",
                   documents=[Document(f"{name} doc", "text")], links=[Link(f"{name} deck", "https://deck.example/x")])

    await env.sync()

    assert {d["recordType"] for d in await env.records()} >= {"PROJECT", "WEBPAGE", "LINK", "FILE"}
    await env.assert_matches_linear()


async def test_a_project_that_leaves_a_team_is_withdrawn_from_it(env: _Env) -> None:
    p = await _people(env, "alice", "bob")
    ws = env.ws
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    legal = ws.team("LEGAL", visibility="private", members=[p["sync"], p["bob"]])
    audit = ws.project("Audit", [sec, legal], content=f"[a.pdf]({ws.upload('a.pdf', b'%PDF')})",
                       documents=[Document("Audit doc", "text")], links=[Link("Audit deck", "https://deck.example/a")])
    await env.sync()
    await env.assert_matches_linear()

    audit.teams, audit.updated_at = [sec], ws.tick()
    await env.sync()
    await env.assert_matches_linear()

    audit.teams, audit.updated_at = [legal], ws.tick()
    await env.sync()
    await env.assert_matches_linear()


# ---------------------------------------------------------------------- moves and deletes

async def test_an_issue_moved_into_a_private_team_takes_everything_with_it(env: _Env) -> None:
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    report = ws.upload("findings.pdf", b"%PDF-1.4 findings")
    issue = ws.issue(
        eng, "Auth bypass in the login flow", description=f"Details in [findings.pdf]({report})",
        comments=[Comment(f"Also see [findings.pdf]({report})", p["alice"])],
        attachments=[Attachment("PR #42", "https://github.com/acme/app/pull/42")],
        documents=[Document("Timeline", "09:00 detected")],
    )
    await env.sync()
    assert {d["recordType"] for d in await env.records()} >= {"TICKET", "LINK", "WEBPAGE", "FILE"}
    await env.assert_matches_linear()

    ws.move(issue, sec)
    await env.sync()

    await env.assert_matches_linear()


async def test_moving_a_parent_issue_takes_its_sub_issues_along(env: _Env) -> None:
    """Linear moves the sub-issues with their parent; their access follows."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    epic = ws.issue(eng, "Incident follow-ups")
    child = ws.issue(eng, "Rotate leaked key", parent=epic, documents=[Document("Steps", "1. revoke")])
    grandchild = ws.issue(eng, "Audit key usage", parent=child)
    await env.sync()
    await env.assert_matches_linear()

    ws.move(epic, sec)
    await env.sync()

    assert {i.team.key for i in (epic, child, grandchild)} == {"SEC"}
    assert await env.ticket_names() == {_name(epic), _name(child), _name(grandchild)}
    await env.assert_matches_linear()


async def test_an_issue_moved_out_of_the_synced_teams_is_removed(env: _Env) -> None:
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    hr = ws.team("HR", visibility="private", members=[p["alice"]])  # the sync account is not in HR
    issue = ws.issue(eng, "Salary bands", attachments=[Attachment("Sheet", "https://docs.example/sheet")])
    await env.sync()
    assert await env.record(issue.id) is not None

    ws.move(issue, hr)
    await env.sync()

    assert await env.record(issue.id) is None, "the moved issue is still stored"
    assert await env.record(issue.attachments[0].id) is None, "the moved issue's attachment is still stored"
    await env.assert_matches_linear()


@pytest.mark.parametrize("how", ["deleted", "sync account removed"])
async def test_a_team_gone_from_linear_takes_its_records_along(env: _Env, how: str) -> None:
    """The team is deleted, or the API key's account leaves the private team: its records
    go, the other teams' stay."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    hr = ws.team("HR", visibility="private", members=[p["sync"], p["alice"]])
    keep = ws.issue(eng, "Roadmap")
    gone = ws.issue(hr, "Salary review", attachments=[Attachment("Sheet", "https://docs.example/s")])
    await env.sync()
    assert await env.record(gone.id) is not None

    if how == "deleted":
        ws.teams.remove(hr)
        ws.issues.remove(gone)
    else:
        hr.members.remove(p["sync"])
    await env.sync()

    assert await env.record(gone.id) is None, "the team's issue is still stored"
    assert await env.record(gone.attachments[0].id) is None, "the team's attachment is still stored"
    assert await env.record(keep.id) is not None
    await env.assert_matches_linear()


async def test_trashing_a_parent_keeps_its_live_sub_issues(env: _Env) -> None:
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    epic = ws.issue(eng, "Search revamp")
    child = ws.issue(eng, "Invoice PDFs", parent=epic, attachments=[Attachment("Spec", "https://docs.example/spec")])
    await env.sync()

    ws.trash(epic)
    await env.sync()

    assert await env.record(epic.id) is None, "the trashed epic is still stored"
    assert await env.record(child.id) is not None, "trashing the epic deleted its live sub-issue"
    assert await env.record(child.attachments[0].id) is not None, "trashing the epic deleted its sub-issue's attachment"
    await env.assert_matches_linear()


# ---------------------------------------------------------------------- the API key stays with Linear

async def test_links_to_other_hosts_are_never_fetched(env: _Env) -> None:
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    real = ws.upload("diagram.png", b"\x89PNG diagram")
    bracketed = ws.upload("spec.pdf", b"%PDF spec")  # Linear writes API-created links as [a](<url>)
    hostile = [
        "https://evil.example/uploads.linear.app/report.pdf",
        "http://169.254.169.254/latest/meta-data/?x=uploads.linear.app",
        "https://uploads.linear.app.evil.example/a/b/c.pdf",
        "https://user@uploads.linear.app@evil.example/a.pdf",
        "http://uploads.linear.app/plain-http.pdf",
        "https://uploads.linear.app:8443/other-port.pdf",
    ]
    links = " ".join(f"[f{n}.pdf]({url})" for n, url in enumerate(hostile))
    ws.issue(eng, "Quarterly report", description=f"{links} and the real ones: [diagram.png]({real}) "
             f"[spec.pdf](<{bracketed}>) [x.pdf](<{hostile[0]}>)",
             comments=[Comment(f"![img]({hostile[0]})", p["alice"])])

    await env.sync()
    assert env.connector is not None
    for doc in await env.records():
        record = await env.processor.get_record_by_external_id(env.connector_id, doc["externalRecordId"])
        with contextlib.suppress(Exception):
            response = await env.connector.stream_record(record)
            async for _ in response.body_iterator:
                pass

    files = {d["externalRecordId"].split("#", 1)[0] for d in await env.records() if d["recordType"] == "FILE"}
    assert files == {real, bracketed}, f"file records: {files} (expected the two Linear uploads only)"
    elsewhere = [url for url, _ in ws.requests if httpx.URL(url).host != "uploads.linear.app"]
    assert not elsewhere, f"the connector fetched other hosts: {elsewhere}"
    assert all(carried for url, carried in ws.requests if url in (real, bracketed)), "an upload was fetched without the key"


# ---------------------------------------------------------------------- sync robustness

async def test_large_teams_and_long_lists_are_read_in_full(env: _Env) -> None:
    """Linear pages every list at 50; the connector must ask for the rest."""
    p = await _people(env, "alice")
    ws = env.ws
    crowd = [ws.user(f"Member{n:02d}") for n in range(60)]
    for person in crowd[-3:]:
        await env.add_user(person.email)  # type: ignore[attr-defined]
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"], *crowd])
    busy = ws.issue(sec, "Busy issue", comments=[Comment(f"comment {n}", p["alice"]) for n in range(70)])
    for n in range(120):
        ws.issue(sec, f"Issue {n:03d}")

    await env.sync()

    missing = {_name(i) for i in ws.issues} - await env.ticket_names()
    assert not missing, f"{len(missing)} of 121 issues were not synced"
    await env.assert_matches_linear()
    assert env.connector is not None
    record = await env.processor.get_record_by_external_id(env.connector_id, busy.id)
    response = await env.connector.stream_record(record)
    body = b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator]).decode()
    missing = [n for n in range(70) if f"comment {n}" not in body]
    assert not missing, f"{len(missing)} of 70 comments are not in the indexed content"


async def test_a_comment_from_an_integration_can_be_indexed(env: _Env) -> None:
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    issue = ws.issue(eng, "Crash on save", comments=[Comment("Linked PR #7 (from GitHub)", None)])
    await env.sync()
    assert env.connector is not None

    record = await env.processor.get_record_by_external_id(env.connector_id, issue.id)
    response = await env.connector.stream_record(record)
    body = b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator]).decode()

    assert "Linked PR #7" in body


async def test_a_comment_written_later_reaches_the_content(env: _Env) -> None:
    """A new comment does not change its issue's updatedAt in Linear."""
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    issue = ws.issue(eng, "Flaky upload test")
    other = ws.issue(eng, "Untouched issue")
    await env.sync()
    assert env.connector is not None
    edited_at = issue.updated_at

    refreshed: list[str] = []  # what the sync queued for re-indexing; streaming reads Linear live
    content_update = env.processor.on_record_content_update

    async def spy(record: Record, permissions: list[Permission] | None = None) -> None:
        refreshed.append(record.external_record_id)
        await content_update(record, permissions)

    env.processor.on_record_content_update = spy  # type: ignore[method-assign]
    now = ws.tick()
    issue.comments.append(Comment("Root cause: clock skew on the runner", p["alice"], created_at=now, updated_at=now))
    await env.sync()

    assert issue.updated_at == edited_at
    assert refreshed == [issue.id], f"queued for re-indexing: {refreshed} (expected the commented issue only)"
    record = await env.processor.get_record_by_external_id(env.connector_id, issue.id)
    response = await env.connector.stream_record(record)
    body = b"".join([c if isinstance(c, bytes) else c.encode() async for c in response.body_iterator]).decode()
    assert "clock skew" in body, "the later comment is not in the indexed content"
    assert await env.record(other.id) is not None
    await env.assert_matches_linear()


async def test_a_revoked_key_keeps_records_and_access(env: _Env) -> None:
    """The key is revoked in Linear: the sync stops and says so, nothing is removed; a new
    key resumes it."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"]])
    ws.issue(eng, "Release notes", attachments=[Attachment("Draft", "https://docs.example/draft")])
    ws.issue(sec, "Key rotation", documents=[Document("Runbook", "steps")])
    await env.sync()
    stored = sorted(d["id"] for d in await env.records())
    assert env.connector is not None

    ws.token = f"lin_api_{uuid.uuid4().hex}"  # the old key no longer works
    with pytest.raises(Exception, match="(?i)authenticat"):
        await env.sync()

    assert sorted(d["id"] for d in await env.records()) == stored, "a rejected key removed records"
    await env.assert_matches_linear()
    assert "credentials" in str(env.connector.notify.call_args_list), "the admin was not told the key was rejected"

    env.config["auth"]["apiToken"], env.connector = ws.token, None  # the admin pastes a new key
    ws.issue(eng, "After the new key")
    await env.sync()
    assert _name(ws.issues[-1]) in await env.ticket_names()
    await env.assert_matches_linear()


@pytest.mark.parametrize("operation", ["teams", "teammembers", "users"])
async def test_failing_to_read_teams_or_people_keeps_access(env: _Env, operation: str) -> None:
    """Linear fails every read of the teams, their members or the users for a whole sync
    (after the retries). Access stays as it was; the next sync catches up."""
    p = await _people(env, "alice", "bob")
    ws = env.ws
    crowd = [ws.user(f"Member{n:03d}") for n in range(120)]  # past the 100 members a team lists inline
    eng = ws.team("ENG", members=[p["sync"], p["alice"], p["bob"]])
    sec = ws.team("SEC", visibility="private", members=[p["sync"], p["alice"], *crowd])
    ws.issue(eng, "Roadmap", attachments=[Attachment("Deck", "https://deck.example/r")])
    ws.issue(sec, "Threat model")
    await env.sync()
    stored = sorted(d["id"] for d in await env.records())

    ws.failing[operation] = lambda _variables: True
    ws.operations.clear()
    with contextlib.suppress(Exception):
        await env.sync()
    assert operation in ws.operations, f"the sync never read {operation}"
    assert sorted(d["id"] for d in await env.records()) == stored, f"failing {operation} removed records"
    await env.assert_matches_linear()

    del ws.failing[operation]
    sec.members.append(p["bob"])
    await env.sync()
    await env.assert_matches_linear()


async def test_a_failed_page_loses_nothing(env: _Env) -> None:
    """A page that fails after the retries holds the checkpoint; the next sync reads it."""
    p = await _people(env, "alice")
    ws = env.ws
    eng = ws.team("ENG", members=[p["sync"], p["alice"]])
    for n in range(120):
        ws.issue(eng, f"Issue {n:03d}")
    await env.sync()
    for issue in ws.issues[:110]:
        ws.change(issue, title=issue.title + " (edited)")

    ws.failing["issues"] = lambda variables: bool(variables.get("after"))  # every later page, every retry
    with contextlib.suppress(Exception):
        await env.sync()
    del ws.failing["issues"]
    await env.sync()

    stale = sorted({_name(i) for i in ws.issues} - await env.ticket_names())
    assert not stale, f"{len(stale)} edits never reached PipesHub, e.g. {stale[:3]}"
