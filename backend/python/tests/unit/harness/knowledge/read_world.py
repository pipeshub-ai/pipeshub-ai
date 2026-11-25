"""QA-27's org in memory: the shared record-access fake seeded with the matrix layout, and the reads the knowledge tools
make around it, each answered from the same records and decided by the same rule.

``KnowledgeReads`` is ``InMemoryRecordAccess`` (C-27) plus what lookup, the Slack tools and the shared view read
besides the record-access surface: typed records by id, external id and link, the knowledge hub's node access (the
fake's own decision, which the integration level holds Neo4j and ArangoDB to), a thread's records, the org's apps.
Publications are the real ``KnowledgePublications`` over an in-memory directory (``publications_over``), its
container listing holding each container's live records, as the graph's listing does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.knowledge.publications import Audience, PublicationDraft
from tests.support.knowledge_publications import ORG, publications_over
from tests.support.knowledge_read_world import (
    EXPIRED_AT,
    MODES,
    NOW,
    World,
    named,
    typed_record,
)
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from app.harness.knowledge.service import KnowledgePublications
    from app.models.entities import Record
    from tests.support.knowledge_read_world import Seeded

SUFFIX = "u27"


class KnowledgeReads(InMemoryRecordAccess):
    def __init__(self, world: World, config_service: object, channel_readers: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.world = world
        self.config_service = config_service
        self.typed: dict[str, Record] = {}
        # Users (by name) holding a role on the Slack channel's record group; QA-27's channel is bob's alone.
        self.channel_readers = channel_readers

    @property
    def channel_group(self) -> str:
        return f"channel-{self.world.suffix}"

    def _user_of_key(self, user_key: str) -> str | None:
        return next((user_id for key, user_id in self.world.users.values() if key == user_key), None)

    async def get_user_by_user_id(self, user_id: str, **_: object) -> dict[str, Any] | None:
        key = next((k for k, uid in self.world.users.values() if uid == user_id), None)
        if key is None:
            return None
        return {"_key": key, "id": key, "userId": user_id, "orgId": self.world.org_id, "email": f"{user_id}@corp.example"}

    async def get_org_apps(self, org_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        return [{"_key": app, "id": app, "type": kind} for app, kind in ((self.world.drive, "Drive"), (self.world.slack, "SLACK"))]

    async def get_record_by_id(self, record_id: str, transaction: str | None = None) -> Record | None:
        return self.typed.get(record_id)

    async def get_typed_records_batch(self, record_ids: list[str]) -> dict[str, Record]:
        return {rid: self.typed[rid] for rid in record_ids if rid in self.typed}

    async def get_record_by_external_id(
        self, connector_id: str, external_id: str, transaction: str | None = None
    ) -> Record | None:
        return next(
            (r for r in self.typed.values() if r.connector_id == connector_id and r.external_record_id == external_id),
            None,
        )

    async def get_records_by_weburls(
        self, weburls: list[str], org_id: str, per_address: int = 2, **_: object
    ) -> list[dict[str, Any]]:
        """Every record of the org stored under one of the addresses (main #5538): the resolver reads each by id."""
        return [{"id": r.id} for r in self.typed.values() if r.weburl in weburls and r.org_id == org_id]

    async def get_knowledge_hub_node_access(
        self, node_id: str, user_key: str, org_id: str, folder_mime_types: list[str], transaction: str | None = None
    ) -> dict[str, Any] | None:
        user_id = self._user_of_key(user_key)
        if node_id == self.channel_group:
            readers = {self.world.user_id(name) for name in self.channel_readers}
            readable = user_id in readers and org_id == self.world.org_id
            return {"id": node_id, "nodeType": "recordGroup", "userRole": "READER"} if readable else None
        record = self.typed.get(node_id)
        if user_id is None or record is None:
            return None
        if await self.check_record_access_with_details(user_id, org_id, node_id) is None:
            return None
        return {
            "id": node_id, "name": record.record_name, "nodeType": "record", "subType": record.record_type.value,
            "connector": record.connector_name.value, "webUrl": record.weburl, "recordType": record.record_type.value,
            "indexingStatus": record.indexing_status, "userRole": "READER",
        }

    async def get_document(self, document_key: str, collection: str, **_: object) -> dict[str, Any] | None:
        document = self.records.get(document_key)
        return dict(document) if document is not None else None

    async def get_edge(self, *_: object, **__: object) -> None:
        """No point lookup of a permission edge: the shared rule decides through the single check."""
        return

    async def get_edges_from_node(
        self, node_id: str, edge_collection: str, transaction: str | None = None
    ) -> list[dict[str, Any]]:
        """BELONGS_TO from a record to the record group it sits in: the container its publication names."""
        collection, _, key = node_id.partition("/")
        seeded = next((s for s in self.world.records.values() if s.id == key), None)
        if edge_collection != "belongsTo" or collection != "records" or seeded is None or seeded.container is None:
            return []
        return [{"_from": node_id, "_to": f"recordGroups/{seeded.container}"}]

    async def get_records_by_record_group(
        self, record_group_id: str, connector_id: str, org_id: str, depth: int, **_: object
    ) -> list[Record]:
        return [r for r in self.typed.values() if r.record_group_id == record_group_id and r.org_id == org_id]

    async def get_record_group_by_external_id(
        self, connector_id: str, external_id: str, **_: object
    ) -> dict[str, Any] | None:
        if (connector_id, external_id) != (self.world.slack, self.world.channel):
            return None
        return {"id": self.channel_group, "_key": self.channel_group, "externalGroupId": external_id, "orgId": ORG}


def seeded_world(config_service: object, channel_readers: frozenset[str] = frozenset()) -> tuple[World, KnowledgeReads]:
    """The layout seeded into the shared rule: alice's records, the group's, the org's, bob's, the trash; the Slack
    channel readable by ``channel_readers`` (by name) besides its owner."""
    world = named(ORG, SUFFIX)
    graph = KnowledgeReads(world, config_service, channel_readers)
    for name in world.users:
        graph.user(
            world.user_id(name), ORG, groups=(world.group,) if name == "alice" else (), key=world.user_key(name)
        )
    for seeded in world.records.values():
        if seeded.cell.ownership == "missing":
            continue
        # As the graph reads it back: the content id is written after the record (``batch_upsert_records`` drops it).
        record = typed_record(world, seeded).model_copy(update={"virtual_record_id": seeded.vrid})
        graph.typed[seeded.id] = record
        graph.record(
            seeded.id, ORG, connectorId=record.connector_id, recordName=seeded.name, virtualRecordId=seeded.vrid,
            indexingStatus="COMPLETED", isDeleted=seeded.cell.ownership == "trashed",
        )
        _grant(graph, world, seeded)
    # The channel's record group, which the one access check decides like any node (main #4255).
    graph.record(graph.channel_group, ORG, connectorId=world.slack, recordType="RECORD_GROUP")
    for name in channel_readers:
        graph.grant(graph.channel_group, user=world.user_id(name))
    return world, graph


def _grant(graph: KnowledgeReads, world: World, seeded: Seeded) -> None:
    ownership = seeded.cell.ownership
    if ownership == "group":
        graph.grant(seeded.id, group=world.group)
    elif ownership == "org":
        graph.grant(seeded.id, org=ORG)
    else:
        graph.grant(seeded.id, user=world.user_id("bob" if ownership == "other" else "alice"), role="OWNER")


async def publications(world: World, graph: KnowledgeReads) -> KnowledgePublications:
    """The world's publications over its containers; a container lists its live records."""
    containers: dict[str, set[str]] = {}
    for seeded in world.records.values():
        if seeded.container is not None and seeded.cell.ownership not in ("missing", "trashed"):
            containers.setdefault(f"recordGroups/{seeded.container}", set()).add(seeded.id)
    service = publications_over(graph, containers, now_ms=NOW)
    for (publication, kind), pid in world.publications.items():
        expired = publication == "expired"
        audience = world.user_id("carol" if publication == "other_audience" else "alice")
        draft = PublicationDraft(
            ORG, f"recordGroups/{world.container(publication, kind)}", Audience(user_ids=frozenset({audience})),
            MODES[publication], world.user_id("bob"), EXPIRED_AT if expired else None,  # type: ignore[arg-type]
        )
        _ = await service.directory.create(draft, publication_id=pid, now_ms=EXPIRED_AT - 1000 if expired else NOW)
        if publication == "revoked":
            _ = await service.directory.revoke(ORG, pid, by=world.user_id("bob"), now_ms=NOW + 1)
    return service


__all__ = ["SUFFIX", "KnowledgeReads", "publications", "seeded_world"]
