"""The seeded org of E2E3 §1.3, written through ``IGraphDBProvider`` (records, users, a group, permissions, trash,
record groups and their publications), with each record's blocks for the blob seam and its points for the vector
store.

Every record carries a marker word of its own, so a test can tell from any text which record reached the engine.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.harness.knowledge.directory import GraphPublicationDirectory
from app.models.entities import FileRecord, RecordType
from app.services.vector_db.models import VectorPoint
from harness_core.knowledge.publications import Audience, PublicationDraft

if TYPE_CHECKING:
    from pathlib import Path

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from tests.unit.harness.engines.matrix.services import MatrixServices

C = CollectionNames
# The single strategy's one collection (``CollectionType.RECORDS``).
COLLECTION = "records"
DIM = 32
NOW = 1_790_000_000_000
QUERY = "quarterly ledger"


@dataclass(frozen=True)
class Seeded:
    """One record of the world: its marker, who may read it, and how."""

    name: str
    marker: str
    blocks: tuple[str, ...]
    owner: str  # alice | bob
    group: str | None = None  # the record group it belongs to (a publication's container)
    readers_group: bool = False  # readable through group ``eng``
    trashed: bool = False
    # TG-16: in local record storage only (``stored``), never embedded: only the storage search layer finds it.
    stored: bool = False


RECORDS = (
    Seeded("rec-alice", "ALPHA7319", (
        "The quarterly ledger for the north region closed at 1200 units.",
        "Marker ALPHA7319: the south region closed at 800 units.",
        "The west region closed at 400 units.",
    ), "alice"),
    Seeded("rec-group", "GAMMA2207", (
        "The engineering quarterly ledger lists 3 open incidents.", "Marker GAMMA2207 belongs to group eng.",
    ), "bob", readers_group=True),
    Seeded("rec-bob", "BRAVO5581", (
        "Bob's private quarterly ledger: salary bands.", "Marker BRAVO5581 is Bob's alone.",
    ), "bob"),
    Seeded("rec-trash", "TANGO8840", (
        "An old quarterly ledger, deleted.", "Marker TANGO8840 is in the trash.",
    ), "alice", trashed=True),
    Seeded("rec-pub-full", "FOXTROT1162", (
        "The published quarterly ledger for partners.", "Marker FOXTROT1162 is published in full.",
    ), "bob", group="rg-full"),
    Seeded("rec-pub-answer", "ECHO4473", (
        "The answer-only quarterly ledger for partners.", "Marker ECHO4473 is published to answer only.",
    ), "bob", group="rg-answer"),
    Seeded("rec-stored", "KILO3307", (
        "An archived audit note kept in record storage.", "Marker KILO3307: the archive audit passed.",
    ), "alice", stored=True),
    Seeded("rec-stored-bob", "LIMA9914", (
        "Bob's archived audit note, marker KILO3307 as well.", "Marker LIMA9914 is Bob's alone.",
    ), "bob", stored=True),
)


def embed(text: str) -> list[float]:
    """A stable bag-of-words hash embedding: texts sharing words point the same way."""
    vector = [0.0] * DIM
    for word in re.findall(r"\w+", text.lower()):
        digest = hashlib.sha256(word.encode()).digest()
        vector[digest[0] % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class HashEmbeddings:
    """The run's dense embedding model (no embedding model is installed for the matrix): ``embed`` above."""

    async def aembed_query(self, text: str) -> list[float]:
        return embed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [embed(t) for t in texts]


@dataclass
class Org:
    org_id: str
    suffix: str
    users: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])  # name -> (key, userId)
    ids: dict[str, str] = field(default_factory=dict[str, str])  # record name -> record id
    vrids: dict[str, str] = field(default_factory=dict[str, str])
    publications: dict[str, str] = field(default_factory=dict[str, str])  # record group -> publication id

    @property
    def connector_id(self) -> str:
        return f"drive-e2e3-{self.suffix}"

    @property
    def group_id(self) -> str:
        return f"grp-eng-{self.suffix}"

    def user_id(self, name: str) -> str:
        return self.users[name][1]

    def user_key(self, name: str) -> str:
        return self.users[name][0]

    def record_group(self, name: str) -> str:
        return f"{name}-{self.suffix}"

    def url(self, name: str) -> str:
        return f"https://drive.example/{self.suffix}/{name}"

    def seeded(self, name: str) -> Seeded:
        return next(r for r in RECORDS if r.name == name)

    def full_record(self, vrid: str) -> dict[str, Any] | None:
        """The record as blob storage returns it."""
        name = next((n for n, v in self.vrids.items() if v == vrid), None)
        if name is None:
            return None
        seeded = self.seeded(name)
        rid = self.ids[name]
        return {
            "virtual_record_id": vrid, "id": rid, "_key": rid, "record_name": f"{name}.txt",
            "context_metadata": f"Record ID: {rid}\nRecord: {name}.txt",
            "block_containers": {
                "blocks": [{"index": i, "type": "text", "data": t} for i, t in enumerate(seeded.blocks)],
                "block_groups": [],
            },
            "origin": "CONNECTOR", "connector_name": Connectors.GOOGLE_DRIVE.value, "mime_type": "text/plain",
            "record_type": "FILE", "version": 1,
        }


def named(org_id: str, suffix: str, *, alice: str | None = None) -> Org:
    """The org's ids, which ``seed`` writes: derived from ``suffix`` alone, so another process can name them."""
    org = Org(org_id, suffix)
    for name in ("alice", "bob", "carol"):
        org.users[name] = (f"uk-{name}-{suffix}", f"{name}-{suffix}")
    if alice is not None:
        org.users["alice"] = (org.users["alice"][0], alice)
    for record in RECORDS:
        org.ids[record.name] = f"{record.name}-{suffix}"
        # Record storage names a file by its virtual record id, which the product makes a UUID (and storage search
        # reads back only that shape).
        org.vrids[record.name] = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{record.name}-{suffix}")) if record.stored
            else f"vr-{record.name}-{suffix}"
        )
    for group in ("rg-full", "rg-answer"):
        org.publications[group] = f"pub-{group}-{suffix}"
    return org


async def seed(graph: IGraphDBProvider, org_id: str, suffix: str, *, alice: str | None = None) -> Org:
    """``alice``: her user id (the run's actor as Node admits it); the others' are derived from ``suffix``."""
    org = named(org_id, suffix, alice=alice)

    await graph.batch_upsert_nodes(
        [{"id": org_id, "accountType": "enterprise", "name": "E2E3 Org", "isActive": True,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.ORGS.value,
    )
    await graph.batch_upsert_nodes(
        [{"id": key, "userId": user_id, "orgId": org_id, "email": f"{user_id}@corp.example", "fullName": name.title(),
          "isActive": True, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for name, (key, user_id) in org.users.items()],
        collection=C.USERS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": key, "from_collection": C.USERS.value, "to_id": org_id, "to_collection": C.ORGS.value,
          "entityType": "ORGANIZATION", "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for key, _ in org.users.values()],
        collection=C.BELONGS_TO.value,
    )
    await graph.batch_upsert_nodes(
        [{"id": org.connector_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace", "scope": "team",
          "isActive": True, "orgId": org_id, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.APPS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": org_id, "from_collection": C.ORGS.value, "to_id": org.connector_id, "to_collection": C.APPS.value,
          "createdAtTimestamp": NOW}],
        collection=C.ORG_APP_RELATION.value,
    )
    await graph.batch_create_edges(
        [{"from_id": key, "from_collection": C.USERS.value, "to_id": org.connector_id, "to_collection": C.APPS.value,
          "syncState": "COMPLETED", "lastSyncUpdate": NOW, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for key, _ in org.users.values()],
        collection=C.USER_APP_RELATION.value,
    )
    await graph.batch_upsert_nodes(
        [{"id": org.group_id, "orgId": org_id, "name": "eng", "groupName": "eng", "externalGroupId": f"ext-{org.group_id}",
          "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": org.connector_id,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}],
        collection=C.GROUPS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": org.user_key(name), "from_collection": C.USERS.value, "to_id": org.group_id,
          "to_collection": C.GROUPS.value, "type": "USER", "role": "READER",
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW} for name in ("alice", "carol")],
        collection=C.PERMISSION.value,
    )
    groups = sorted({r.group for r in RECORDS if r.group is not None})
    await graph.batch_upsert_nodes(
        [{"id": org.record_group(g), "orgId": org_id, "groupName": g, "externalGroupId": f"ext-{g}-{suffix}",
          "groupType": "DRIVE", "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": org.connector_id,
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW} for g in groups],
        collection=C.RECORD_GROUPS.value,
    )
    await graph.batch_create_edges(
        [{"from_id": org.user_key("bob"), "from_collection": C.USERS.value, "to_id": org.record_group(g),
          "to_collection": C.RECORD_GROUPS.value, "type": "USER", "role": "OWNER",
          "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW} for g in groups],
        collection=C.PERMISSION.value,
    )

    await graph.batch_upsert_records([_file(org, r) for r in RECORDS])
    for record in RECORDS:
        await graph.update_node(org.ids[record.name], C.RECORDS.value, {"virtualRecordId": org.vrids[record.name]})

    def permission(frm: str, frm_collection: str, record: str, role: str, kind: str) -> dict[str, Any]:
        return {"from_id": frm, "from_collection": frm_collection, "to_id": org.ids[record],
                "to_collection": C.RECORDS.value, "role": role, "type": kind,
                "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}

    edges = [permission(org.user_key(r.owner), C.USERS.value, r.name, "OWNER", "USER") for r in RECORDS]
    edges += [permission(org.group_id, C.GROUPS.value, r.name, "READER", "GROUP") for r in RECORDS if r.readers_group]
    await graph.batch_create_edges(edges, collection=C.PERMISSION.value)
    await graph.batch_create_edges(
        [{"from_id": org.ids[r.name], "from_collection": C.RECORDS.value, "to_id": org.record_group(r.group),
          "to_collection": C.RECORD_GROUPS.value, "entityType": "GROUP", "createdAtTimestamp": NOW,
          "updatedAtTimestamp": NOW} for r in RECORDS if r.group is not None],
        collection=C.BELONGS_TO.value,
    )
    await graph.batch_create_edges(
        [{"from_id": org.ids[r.name], "from_collection": C.RECORDS.value, "to_id": org.record_group(r.group),
          "to_collection": C.RECORD_GROUPS.value, "createdAtTimestamp": NOW, "updatedAtTimestamp": NOW}
         for r in RECORDS if r.group is not None],
        collection=C.INHERIT_PERMISSIONS.value,
    )

    directory = GraphPublicationDirectory(graph)
    for group, mode in (("rg-full", "full"), ("rg-answer", "answer_only")):
        pid = org.publications[group]
        draft = PublicationDraft(
            org_id, f"{C.RECORD_GROUPS.value}/{org.record_group(group)}",
            Audience(user_ids=frozenset({org.user_id("alice")})), mode, org.user_id("bob"),  # type: ignore[arg-type]
        )
        await directory.create(draft, publication_id=pid, now_ms=NOW)

    return org


async def seed_vectors(org: Org, services: MatrixServices) -> None:
    """Each embedded record's blocks as the indexer writes them: the collection through the registry (its payload
    indexes too, which a real store filters on), the point ids and payloads from the indexer's own builders."""
    from app.modules.transformers.vectorstore import deterministic_point_id
    from app.services.vector_db.membership import vector_point_payload
    from app.services.vector_db.strategy import RecordContext

    name = await services.collection_registry().ensure_collection(
        RecordContext(org_id=org.org_id, connector_id=org.connector_id), DIM
    )
    assert name == COLLECTION, name
    points: list[VectorPoint] = []
    for record in (r for r in RECORDS if not r.stored):
        vrid = org.vrids[record.name]
        for i, text in enumerate(record.blocks):
            metadata = {"orgId": org.org_id, "virtualRecordId": vrid, "blockIndex": i, "isBlockGroup": False,
                        "connectorId": org.connector_id}
            points.append(VectorPoint(id=deterministic_point_id(metadata, text), dense_vector=embed(text),
                                      payload=vector_point_payload(metadata, text)))
    await services.vectors.upsert_points(COLLECTION, points)
    await _searchable(services, org, len(points))


async def _searchable(services: MatrixServices, org: Org, count: int, timeout_s: float = 90.0) -> None:
    """Until a search sees every point. A write is visible to search once the store refreshes: at once for the fake,
    within the index's refresh interval for OpenSearch (30 s, by design, so ingest builds fewer segments)."""
    import asyncio
    import time

    vectors = services.vectors
    only_org = await vectors.filter_collection(must={"orgId": org.org_id})
    deadline = time.monotonic() + timeout_s
    while True:
        seen = (await vectors.scroll(COLLECTION, only_org, limit=count + 1)).points
        if len(seen) >= count:
            return
        assert time.monotonic() < deadline, f"{vectors.get_service_name()}: {len(seen)} of {count} points searchable"
        await asyncio.sleep(1.0)


def store(org: Org, mount: Path) -> None:
    """Local record storage as Node's storage service files it (``{mount}/{orgId}/PipesHub/records/{connectorId}``,
    ``record_{virtualRecordId}.json``), for the records kept only there."""
    directory = mount / org.org_id / "PipesHub" / "records" / org.connector_id
    directory.mkdir(parents=True, exist_ok=True)
    for record in (r for r in RECORDS if r.stored):
        vrid = org.vrids[record.name]
        (directory / f"record_{vrid}.json").write_text(json.dumps(org.full_record(vrid)))


def _file(org: Org, record: Seeded) -> FileRecord:
    fields: dict[str, Any] = {
        "id": org.ids[record.name],
        "org_id": org.org_id,
        "record_name": f"{record.name}.txt",
        "record_type": RecordType.FILE,
        "external_record_id": f"ext-{org.ids[record.name]}",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": org.connector_id,
        "mime_type": "text/plain",
        "weburl": org.url(record.name),
        "indexing_status": ProgressStatus.COMPLETED.value,
        "is_file": True,
        "extension": "txt",
        "size_in_bytes": sum(len(b) for b in record.blocks),
    }
    if record.trashed:
        fields.update(
            is_deleted=True, deleted_at=NOW - 1000, deleted_by_user_id=org.user_key("alice"),
            delete_source=DeleteSource.USER, delete_batch_id=f"batch-{org.suffix}",
        )
    return FileRecord(**fields)


async def remove(graph: Any, org: Org) -> None:  # noqa: ANN401 - Neo4jProvider
    """Every node the seed wrote (the lane's own Neo4j goes with the module; this keeps re-seeding clean)."""
    ids = [org.org_id, org.connector_id, org.group_id, *org.ids.values(), *(k for k, _ in org.users.values()),
           *(org.record_group(g) for g in ("rg-full", "rg-answer")), *org.publications.values()]
    await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})


__all__ = ["COLLECTION", "QUERY", "RECORDS", "HashEmbeddings", "Org", "Seeded", "embed", "named", "remove", "seed", "seed_vectors", "store"]
