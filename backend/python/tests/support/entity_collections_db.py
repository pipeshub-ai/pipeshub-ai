"""A vector DB holding several entity collections, each a
``FakeEntityVectorDB`` with its own dimension and points, for stores that keep
one collection per org. Records which collections were created, dropped and
searched, so a test can show an operation stayed inside its org's collection.
"""

from __future__ import annotations

from typing import Any

from app.services.vector_db.models import (
    CollectionConfig,
    HybridSearchRequest,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorPoint,
)
from tests.support.entity_vector_db import FakeEntityVectorDB


class FakeEntityCollections:
    def __init__(self) -> None:
        self.collections: dict[str, FakeEntityVectorDB] = {}
        self.created: list[str] = []
        self.dropped: list[str] = []
        self.searched: list[str] = []

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities()

    def _of(self, name: str) -> FakeEntityVectorDB:
        # A missing collection answers as an empty FakeEntityVectorDB does:
        # "not found" on every read and write.
        return self.collections.get(name) or FakeEntityVectorDB()

    def _entity_points(self, collection: str) -> list:
        # The store's collection stamp is a point with no entity type.
        return [p for p in self.collections[collection].points.values() if p.payload["metadata"].get("entityType")]

    def names(self, collection: str) -> set[str]:
        return {p.payload["metadata"]["name"] for p in self._entity_points(collection)}

    def vectors(self, collection: str) -> set[float]:
        return {(p.dense_vector or [0.0])[0] for p in self._entity_points(collection)}

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        return await self._of(collection_name).get_collection_info(collection_name)

    async def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    async def create_collection(self, collection_name: str, config: CollectionConfig) -> None:
        if collection_name in self.collections:
            raise RuntimeError(f"Collection {collection_name} already exists")
        self.collections[collection_name] = FakeEntityVectorDB(config.embedding_size)
        self.created.append(collection_name)

    async def delete_collection(self, collection_name: str) -> None:
        await self._of(collection_name).delete_collection(collection_name)
        del self.collections[collection_name]
        self.dropped.append(collection_name)

    async def create_index(self, collection_name: str, field_name: str, field_schema: dict) -> None:
        await self._of(collection_name).create_index(collection_name, field_name, field_schema)

    async def filter_collection(self, **conditions: Any) -> dict[str, dict[str, Any]]:  # noqa: ANN401
        return await FakeEntityVectorDB().filter_collection(**conditions)

    async def scroll(self, collection_name: str, **kwargs: Any) -> ScrollResult:  # noqa: ANN401
        return await self._of(collection_name).scroll(collection_name, **kwargs)

    def scroll_offset_after_delete(self, next_offset: str | None, deleted: int) -> str | None:
        return next_offset

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        return await self._of(collection_name).retrieve_points(collection_name, ids)

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        await self._of(collection_name).upsert_points(collection_name, points)

    async def update_payload_by_ids(self, collection_name: str, ids: list[str], payload: dict) -> None:
        await self._of(collection_name).update_payload_by_ids(collection_name, ids, payload)

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest],
    ) -> list[list[SearchResult]]:
        self.searched.append(collection_name)
        return await self._of(collection_name).query_nearest_points(collection_name, requests)

    async def delete_points(
        self, collection_name: str, filter: dict[str, dict[str, Any]], refresh: bool = False,  # noqa: A002
    ) -> None:
        db = self._of(collection_name)
        db._require(collection_name)
        for point in db._matching(filter):
            del db.points[point.id]
