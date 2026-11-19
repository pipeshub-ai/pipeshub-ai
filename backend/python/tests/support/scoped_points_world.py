"""Points of one virtual record written in two scopes, and the questions asked of them.

The scope is a test stub of ``WriteScope``: its points carry the virtual record's id and a ``scopeGroup`` payload field that
tells the scopes apart, and its point ids carry a salt. Shared by the unit test (in-memory store) and the real-store test
(Qdrant, OpenSearch, Redis), so both check the same thing: a write to one scope never reaches another.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.modules.transformers.vectorstore import VectorStore, deterministic_point_id
from app.services.vector_db.const.const import BLOCK_ID_FIELD, PAYLOAD_KEYWORD_INDEXES
from app.services.vector_db.membership import vector_point_payload
from app.services.vector_db.models import CollectionConfig, DistanceMetric, VectorPoint
from app.services.vector_db.write_scope import WholeRecordScope
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    from app.services.vector_db.interface.vector_db import IVectorDBService

ORG = "5f0c1d2e3a4b5c6d7e8f9a0b"
V = str(uuid.uuid5(uuid.NAMESPACE_URL, "scoped-world/V"))
OTHER = str(uuid.uuid5(uuid.NAMESPACE_URL, "scoped-world/other-record"))
BLOCKS = ("b0", "b1", "b2")  # the same block ids in every scope: only the scope tells them apart
DIM = 8
GROUP_FIELD = "scopeGroup"
GROUP_PATH = f"metadata.{GROUP_FIELD}"
GROUP_INDEX = ((GROUP_PATH, {"type": "keyword"}),)


@dataclass(frozen=True)
class StubScope:
    """A write that owns the points of one group of a virtual record."""

    group: str
    content_vrid: str = V
    read_vrid: str = V
    is_scoped: bool = True
    writes_summary: bool = False
    reconciles: bool = False

    @property
    def point_salt(self) -> str:
        return f"salt-{self.group}"

    def must(self) -> dict[str, Any]:
        return {"virtualRecordId": self.read_vrid, GROUP_FIELD: self.group}

    def payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {**metadata, "virtualRecordId": self.read_vrid, GROUP_FIELD: self.group}


FIRST = StubScope("one")
SECOND = StubScope("two")
WHOLE = WholeRecordScope.of(OTHER)
SCOPES = {"first": FIRST, "second": SECOND, "other": WHOLE}


def points_of(scope: StubScope | WholeRecordScope, *, slot: int = 0) -> list[VectorPoint]:
    out = []
    for i, block in enumerate(BLOCKS):
        content = {"virtualRecordId": scope.content_vrid, "blockId": block, "blockIndex": i, "orgId": ORG,
                   "isBlock": True, "isBlockGroup": False}
        text = f"{scope.content_vrid} {block}"
        dense = [1.0 if j == (i + slot) % DIM else 0.0 for j in range(DIM)]
        out.append(VectorPoint(id=deterministic_point_id(content, text, scope.point_salt), dense_vector=dense,
                               payload=vector_point_payload(scope.payload(content), text)))
    return out


def store_over(service: IVectorDBService) -> VectorStore:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"_key": "r"})
    return VectorStore(logger=MagicMock(), config_service=AsyncMock(), graph_provider=graph,
                       collection_registry=make_collection_registry(), vector_db_service=service)


async def fresh(service: IVectorDBService, collection: str, *, index_group: bool = True, index_block: bool = True) -> None:
    await service.create_collection(collection, CollectionConfig(embedding_size=DIM, distance_metric=DistanceMetric.COSINE))
    fields = (*(f for f in PAYLOAD_KEYWORD_INDEXES if index_block or f[0] != BLOCK_ID_FIELD), *(GROUP_INDEX if index_group else ()))
    for field_name, schema in fields:
        try:
            await service.create_index(collection, field_name, schema)
        except Exception as e:
            if "exist" not in str(e).lower():
                raise
    await seed(service, collection)
    if index_group:
        assert await census(service, collection) == FULL


async def seed(service: IVectorDBService, collection: str) -> None:
    for slot, scope in enumerate(SCOPES.values()):
        await service.upsert_points(collection, points_of(scope, slot=slot))


async def census(service: IVectorDBService, collection: str) -> dict[str, int]:
    """Points per scope (filtered by the record and, for a scoped one, its group)."""
    await service.make_searchable(collection)
    out = {}
    for name, scope in SCOPES.items():
        must = {"orgId": ORG, "virtualRecordId": scope.read_vrid, **({GROUP_FIELD: scope.group} if scope.is_scoped else {})}
        out[name] = await service.count_points(collection, await service.filter_collection(must=must))
    return out


FULL = {"first": 3, "second": 3, "other": 3}
