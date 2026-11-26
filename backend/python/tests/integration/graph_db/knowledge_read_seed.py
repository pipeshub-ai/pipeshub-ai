"""QA-27's org written to a real graph through ``IGraphDBProvider``'s own writes (users, a group, the org,
permissions, the trash, record groups and their publications), with one block per record for the vector store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.harness.knowledge.directory import GraphPublicationDirectory
from app.models.entities import RecordGroupType
from app.services.vector_db.models import CollectionConfig, DistanceMetric, VectorPoint
from harness_core.knowledge.publications import Audience, PublicationDraft
from tests.support.knowledge_read_matrix import PUBLICATIONS
from tests.support.knowledge_read_world import (
    EXPIRED_AT,
    MODES,
    NOW,
    Seeded,
    World,
    named,
    typed_record,
)
from tests.unit.harness.engines.matrix.org import COLLECTION, DIM, embed

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from tests.support.in_memory_vector_db import InMemoryVectorDBService

C = CollectionNames


async def seed(graph: IGraphDBProvider, vectors: InMemoryVectorDBService, org_id: str, suffix: str) -> World:
    world = named(org_id, suffix)
    await _org(graph, world)
    await _containers(graph, world)
    live = [s for s in world.records.values() if s.cell.ownership != "missing"]
    await graph.batch_upsert_records([typed_record(world, s) for s in live])
    for seeded in live:
        await graph.update_node(seeded.id, C.RECORDS.value, {"virtualRecordId": seeded.vrid})
    await graph.batch_create_edges([_permission(world, s) for s in live], collection=C.PERMISSION.value)
    contained = [s for s in live if s.container is not None]
    for collection in (C.BELONGS_TO.value, C.INHERIT_PERMISSIONS.value):
        await graph.batch_create_edges(
            [{"from_id": s.id, "from_collection": C.RECORDS.value, "to_id": s.container,
              "to_collection": C.RECORD_GROUPS.value, "entityType": "GROUP", "createdAtTimestamp": NOW,
              "updatedAtTimestamp": NOW} for s in contained],
            collection=collection,
        )
    await _publish(graph, world)
    await vectors.create_collection(COLLECTION, CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE))
    await vectors.upsert_points(COLLECTION, [
        VectorPoint(
            id=f"{s.vrid}-0",
            dense_vector=embed(s.content),
            payload={
                "page_content": s.content,
                "metadata": {"orgId": org_id, "virtualRecordId": s.vrid, "blockIndex": 0, "isBlockGroup": False,
                             "connectorId": world.drive if s.kind == "drive" else world.slack},
            },
        )
        for s in live
    ])
    return world


async def _org(graph: IGraphDBProvider, world: World) -> None:
    org = world.org_id
    await graph.batch_upsert_nodes(
        [{"id": org, "accountType": "enterprise", "name": "QA-27 Org", "isActive": True,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.ORGS.value,
    )
    await graph.batch_upsert_nodes(
        [{"id": key, "userId": user_id, "orgId": org, "email": f"{user_id}@corp.example", "fullName": name.title(),
          "isActive": True, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for name, (key, user_id) in world.users.items()],
        collection=C.USERS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": key, "from_collection": C.USERS.value, "to_id": org, "to_collection": C.ORGS.value,
          "entityType": "ORGANIZATION", "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for key, _ in world.users.values()],
        collection=C.BELONGS_TO.value,
    )
    apps = ((world.drive, "Drive", "Drive", "Google Workspace"), (world.slack, "Slack", Connectors.SLACK.value, "Slack"))
    await graph.batch_upsert_nodes(
        [{"id": app, "name": name, "type": kind, "appGroup": group, "scope": "team", "isActive": True,
          "isConfigured": True, "orgId": org, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for app, name, kind, group in apps],
        collection=C.APPS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": org, "from_collection": C.ORGS.value, "to_id": app, "to_collection": C.APPS.value,
          "createdAtTimestamp": NOW} for app, *_ in apps],
        collection=C.ORG_APP_RELATION.value,
    )
    await graph.batch_create_edges(
        [{"from_id": key, "from_collection": C.USERS.value, "to_id": app, "to_collection": C.APPS.value,
          "syncState": "COMPLETED", "lastSyncUpdate": NOW, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for key, _ in world.users.values() for app, *_ in apps],
        collection=C.USER_APP_RELATION.value,
    )
    await graph.batch_upsert_nodes(
        [{"id": world.group, "orgId": org, "name": "eng", "groupName": "eng", "externalGroupId": f"ext-{world.group}",
          "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": world.drive,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.GROUPS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": world.user_key("alice"), "from_collection": C.USERS.value, "to_id": world.group,
          "to_collection": C.GROUPS.value, "type": "USER", "role": "READER",
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.PERMISSION.value,
    )


async def _containers(graph: IGraphDBProvider, world: World) -> None:
    """A Drive folder group per published column; the Slack channel and a thread per column, all bob's."""
    groups: list[dict[str, Any]] = [
        {"id": world.container(p, "drive"), "orgId": world.org_id, "groupName": f"drive {p}",
         "externalGroupId": f"ext-{world.container(p, 'drive')}", "groupType": "DRIVE",
         "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": world.drive,
         "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
        for p in PUBLICATIONS[1:]
    ]
    groups.append(
        {"id": f"channel-{world.suffix}", "orgId": world.org_id, "groupName": "general", "externalGroupId": world.channel,
         "groupType": RecordGroupType.SLACK_CHANNEL.value, "connectorName": Connectors.SLACK.value,
         "connectorId": world.slack, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
    )
    groups += [
        {"id": world.container(p, "slack"), "orgId": world.org_id, "groupName": f"thread {p}",
         "externalGroupId": world.thread(p), "groupType": RecordGroupType.SLACK_THREAD.value,
         "connectorName": Connectors.SLACK.value, "connectorId": world.slack,
         "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
        for p in PUBLICATIONS
    ]
    await graph.batch_upsert_nodes(groups, collection=C.RECORD_GROUPS.value)
    await graph.batch_create_edges(
        [{"from_id": world.user_key("bob"), "from_collection": C.USERS.value, "to_id": g["id"],
          "to_collection": C.RECORD_GROUPS.value, "type": "USER", "role": "OWNER",
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW} for g in groups],
        collection=C.PERMISSION.value,
    )


def _permission(world: World, seeded: Seeded) -> dict[str, Any]:
    ownership = seeded.cell.ownership
    if ownership == "group":
        frm, collection, kind, role = world.group, C.GROUPS.value, "GROUP", "READER"
    elif ownership == "org":
        frm, collection, kind, role = world.org_id, C.ORGS.value, "ORG", "READER"
    elif ownership == "other":
        frm, collection, kind, role = world.user_key("bob"), C.USERS.value, "USER", "OWNER"
    else:
        frm, collection, kind, role = world.user_key("alice"), C.USERS.value, "USER", "OWNER"
    return {"from_id": frm, "from_collection": collection, "to_id": seeded.id, "to_collection": C.RECORDS.value,
            "role": role, "type": kind, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}


async def _publish(graph: IGraphDBProvider, world: World) -> None:
    directory = GraphPublicationDirectory(graph)
    for (publication, kind), pid in world.publications.items():
        audience = world.user_id("carol" if publication == "other_audience" else "alice")
        expired = publication == "expired"
        draft = PublicationDraft(
            world.org_id, f"{C.RECORD_GROUPS.value}/{world.container(publication, kind)}",
            Audience(user_ids=frozenset({audience})), MODES[publication], world.user_id("bob"),  # type: ignore[arg-type]
            EXPIRED_AT if expired else None,
        )
        _ = await directory.create(draft, publication_id=pid, now_ms=EXPIRED_AT - 1000 if expired else NOW)
        if publication == "revoked":
            _ = await directory.revoke(world.org_id, pid, by=world.user_id("bob"), now_ms=NOW + 1)



__all__ = ["seed"]
