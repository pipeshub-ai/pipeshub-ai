"""The seeded fixture tenant (G-49): one organization with Slack, Jira, Google Drive and Confluence records and
varied access, defined once in ``tests/fixtures/tenant/<name>.yaml`` and loaded wherever a test needs it.

- ``record_access(actor=...)``: the in-memory record-access fake (held to the contract Neo4j and ArangoDB are held to),
  with one tenant user standing in for a driven run's actor.
- ``knowledge()`` / ``connectors()`` / ``world_fixtures()``: the records as the replay drivers' knowledge base and
  fake connector toolsets serve them.
- ``seed_graph(graph)``: the same tenant written to a real graph through ``IGraphDBProvider``'s own methods, so the
  real-backend tests ask Neo4j and ArangoDB what each user reads.

``expect`` in the file is the access each user must get; ``check_expectations`` holds the fake to it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from tests.harness_replay.world import Doc, SlackMessage, Ticket, WorldFixtures
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

TENANT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tenant"
RECORD_PREFIX = "rec-"


class _Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class App(_Fixture):
    id: str
    connector: Literal["SLACK", "JIRA", "DRIVE", "CONFLUENCE"]
    name: str


class Group(_Fixture):
    id: str
    name: str


class User(_Fixture):
    id: str
    name: str
    groups: list[str] = []
    # The connector instances the user may read from; ``None``: every one.
    apps: list[str] | None = None


class Grant(_Fixture):
    user: str | None = None
    group: str | None = None
    role: str = "READER"


class TicketFields(_Fixture):
    key: str
    status: str
    priority: str
    owner: str


class Record(_Fixture):
    id: str
    app: str
    title: str
    text: str
    grants: list[Grant]
    ticket: TicketFields | None = None
    deleted: bool = False

    @property
    def record_id(self) -> str:
        """The graph record id; ``id`` is its virtual record id (as the replay knowledge base names them)."""
        return f"{RECORD_PREFIX}{self.id}"


class Tenant(_Fixture):
    org: str
    domain: str
    apps: list[App]
    groups: list[Group]
    users: list[User]
    records: list[Record]
    expect: dict[str, list[str]]

    def app(self, app_id: str) -> App:
        return next(a for a in self.apps if a.id == app_id)

    def user(self, user_id: str) -> User:
        return next(u for u in self.users if u.id == user_id)

    def record(self, record_id: str) -> Record:
        return next(r for r in self.records if r.id == record_id)

    def email(self, user_id: str) -> str:
        return f"{user_id}@{self.domain}"

    def readable(self, user_id: str) -> frozenset[str]:
        return frozenset(self.expect[user_id])

    def connector_of(self, record_id: str) -> str:
        return self.app(self.record(record_id).app).connector

    # --- the in-memory fake ---------------------------------------------------------------------------------

    def record_access(
        self, *, org: str | None = None, actor: str | None = None, actor_id: str | None = None,
        actor_key: str | None = None,
    ) -> InMemoryRecordAccess:
        """The tenant in the fake, under ``org`` (a driven run's own org; the tenant's by default). ``actor``: the
        tenant user a driven run acts as, seeded under the run's own ``actor_id`` and graph ``actor_key`` (the other
        users keep their ids)."""
        org = org or self.org
        access = InMemoryRecordAccess()
        rename = {actor: actor_id} if actor is not None and actor_id is not None else {}
        for user in self.users:
            uid = rename.get(user.id, user.id)
            key = actor_key if user.id == actor and actor_key is not None else None
            access.user(
                uid, org, groups=tuple(user.groups), apps=None if user.apps is None else tuple(user.apps), key=key
            )
        for record in self.records:
            app = self.app(record.app)
            access.record(
                record.record_id, org, connectorId=app.id, connectorName=app.connector, recordName=record.title,
                virtualRecordId=record.id, origin="CONNECTOR", isDeleted=record.deleted,
            )
            for grant in record.grants:
                if grant.user is not None:
                    _ = access.grant(record.record_id, user=rename.get(grant.user, grant.user), role=grant.role)
                else:
                    _ = access.grant(record.record_id, group=grant.group, role=grant.role)
        return access

    async def check_expectations(self) -> None:
        """The declared ``expect`` is what the access rule decides, for every user and record."""
        access = self.record_access()
        ids = [r.record_id for r in self.records]
        for user in self.users:
            found = await access.check_access(user.id, self.org, node_ids=ids)
            got = {rid.removeprefix(RECORD_PREFIX) for rid in found.node_ids}
            assert got == self.readable(user.id), (user.id, sorted(got ^ self.readable(user.id)))

    # --- the replay drivers' world --------------------------------------------------------------------------

    def knowledge(self) -> list[Doc]:
        """Every live record, as the knowledge base indexes it (access is decided at search time)."""
        return [Doc(id=r.id, title=r.title, text=r.text) for r in self.records if not r.deleted]

    def connectors(self) -> dict[str, str]:
        """Knowledge doc id -> the connector its record came from (what citations and the untrusted taint read)."""
        return {r.id: self.app(r.app).connector for r in self.records}

    def world_fixtures(self, user_id: str) -> WorldFixtures:
        """What ``user_id``'s connector toolsets return: the tickets and Slack messages the user may read."""
        readable = self.readable(user_id)
        tickets = [
            Ticket(key=r.ticket.key, summary=r.title.split(" ", 1)[1], status=r.ticket.status, owner=r.ticket.owner,
                   description=r.text)
            for r in self.records
            if r.ticket is not None and r.id in readable
        ]
        slack: dict[str, list[SlackMessage]] = {}
        for r in self.records:
            if r.id in readable and self.app(r.app).connector == "SLACK":
                channel = r.title.split(",", 1)[0]
                slack.setdefault(channel, []).append(SlackMessage(user="U-ravi", text=r.text))
        return WorldFixtures(knowledge=self.knowledge(), tickets=tickets, slack=slack)

    # --- a real graph ---------------------------------------------------------------------------------------

    async def seed_graph(self, graph: IGraphDBProvider) -> SeededGraph:
        """Writes the tenant to ``graph`` under fresh ids (a shared database keeps every run's), through the
        provider's own node and edge writes, as the record-access contract's real-backend test seeds."""
        from app.config.constants.arangodb import CollectionNames as C

        tag = uuid.uuid4().hex[:8]
        org = f"{self.org}-{tag}"
        apps = {a.id: f"{a.id}-{tag}" for a in self.apps}
        groups = {g.id: f"{g.id}-{tag}" for g in self.groups}
        users = {u.id: (f"uk-{u.id}-{tag}", f"{u.id}-{tag}") for u in self.users}
        records = {r.id: f"{r.record_id}-{tag}" for r in self.records}

        async def node(collection: str, doc: dict[str, object]) -> None:
            assert await graph.batch_upsert_nodes([doc], collection) is not False

        async def edge(collection: str, frm: tuple[str, str], to: tuple[str, str], **extra: object) -> None:
            doc = {"from_id": frm[1], "from_collection": frm[0], "to_id": to[1], "to_collection": to[0],
                   "createdAtTimestamp": 1, **extra}
            assert await graph.batch_create_edges([doc], collection)

        await node(C.ORGS.value, {"id": org, "accountType": "enterprise", "isActive": True})
        for app in self.apps:
            # The connector gate reads only the org's own apps (``app.orgId``).
            await node(C.APPS.value, {"id": apps[app.id], "orgId": org, "name": app.name, "type": app.connector,
                                      "appGroup": "g", "scope": "team", "isActive": True, "createdAtTimestamp": 1})
            await edge(C.ORG_APP_RELATION.value, (C.ORGS.value, org), (C.APPS.value, apps[app.id]))
        for group in self.groups:
            await node(C.GROUPS.value, {"id": groups[group.id], "name": group.name, "orgId": org, "createdAtTimestamp": 1})
        for user in self.users:
            key, uid = users[user.id]
            await node(C.USERS.value, {"id": key, "userId": uid, "orgId": org, "email": self.email(user.id),
                                       "fullName": user.name, "isActive": True})
            await edge(C.BELONGS_TO.value, (C.USERS.value, key), (C.ORGS.value, org), entityType="ORGANIZATION")
            for app_id in user.apps if user.apps is not None else [a.id for a in self.apps]:
                await edge(C.USER_APP_RELATION.value, (C.USERS.value, key), (C.APPS.value, apps[app_id]),
                           syncState="COMPLETED", lastSyncUpdate=1)
            for group_id in user.groups:
                await edge(C.PERMISSION.value, (C.USERS.value, key), (C.GROUPS.value, groups[group_id]),
                           type="USER", role="READER")
        for record in self.records:
            rid, app = records[record.id], self.app(record.app)
            await node(C.RECORDS.value, {
                "id": rid, "orgId": org, "recordName": record.title, "externalRecordId": rid, "recordType": "FILE",
                "origin": "CONNECTOR", "connectorName": app.connector, "connectorId": apps[app.id],
                "virtualRecordId": f"v-{rid}", "isDeleted": record.deleted, "indexingStatus": "COMPLETED",
                "version": 1, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            })
            await node(C.FILES.value, {"id": rid, "name": record.title, "isFile": True})
            await edge(C.IS_OF_TYPE.value, (C.RECORDS.value, rid), (C.FILES.value, rid))
            for grant in record.grants:
                if grant.user is not None:
                    frm, kind = (C.USERS.value, users[grant.user][0]), "USER"
                else:
                    assert grant.group is not None
                    frm, kind = (C.GROUPS.value, groups[grant.group]), "GROUP"
                await edge(C.PERMISSION.value, frm, (C.RECORDS.value, rid), type=kind, role=grant.role)
        return SeededGraph(tag=tag, org=org, users=users, records=records)


@dataclass(frozen=True)
class SeededGraph:
    """The ids the tenant got in a real graph (each ends with ``-<tag>``): user -> (graph key, user id), record ->
    graph record id."""

    tag: str
    org: str
    users: dict[str, tuple[str, str]]
    records: dict[str, str]

    def tenant_ids(self, graph_ids: set[str] | frozenset[str]) -> frozenset[str]:
        back = {v: k for k, v in self.records.items()}
        return frozenset(back[g] for g in graph_ids if g in back)


@cache
def load_tenant(name: str = "atlas") -> Tenant:
    raw = yaml.safe_load((TENANT_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    return Tenant.model_validate(raw)


__all__ = ["RECORD_PREFIX", "SeededGraph", "Tenant", "load_tenant"]
