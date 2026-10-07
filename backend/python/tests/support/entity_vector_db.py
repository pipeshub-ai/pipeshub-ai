"""In-process stand-ins for what ``EntityVectorStore`` talks to: the vector DB
collection and the embedding clients.

The collection keeps a fixed dense dimension and rejects a vector or query of
another, as Qdrant, Redis and OpenSearch do; a recreate empties it. Each
embedding client fills its vectors with its own value, so a test can tell
which model wrote a point or embedded a query.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app.services.vector_db.models import (
    CollectionConfig,
    HybridSearchRequest,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorPoint,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeEmbeddingModel:
    def __init__(self, value: float, dimension: int) -> None:
        self.value = value
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [self.value] * self.dimension


@contextmanager
def embedding_models(models: dict[str, FakeEmbeddingModel]) -> Iterator[None]:
    """Build the client named by a config's ``model`` from ``models``."""
    with patch(
        "app.modules.transformers.entity_vectorstore.get_embedding_model",
        side_effect=lambda provider, config: models[config["configuration"]["model"]],
    ):
        yield


def _field(payload: dict[str, Any], path: str) -> object:
    value: object = payload
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches_value(stored: object, wanted: object) -> bool:
    wanted_values = wanted if isinstance(wanted, list) else [wanted]
    stored_values = stored if isinstance(stored, list) else [stored]
    return any(value in wanted_values for value in stored_values)


class FakeEntityVectorDB:
    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension
        self.points: dict[str, VectorPoint] = {}
        self.deletions = 0
        self.searches: list[HybridSearchRequest] = []

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities()

    @property
    def entity_points(self) -> dict[str, VectorPoint]:
        """The points that are entities: all but the collection's stamp."""
        return {
            point_id: point for point_id, point in self.points.items()
            if (point.payload.get("metadata") or {}).get("entityId")
        }

    @property
    def stamp(self) -> str | None:
        """What ``EntityVectorStore.collection_stamp`` wrote, if it is still here."""
        stamps = [
            stamp for point in self.points.values()
            if (stamp := (point.payload.get("metadata") or {}).get("indexStamp"))
        ]
        return stamps[0] if stamps else None

    def _require(self, collection_name: str) -> None:
        if self.dimension is None:
            raise RuntimeError(f"Collection {collection_name} not found")

    def _require_dimension(self, vector: list[float] | None) -> None:
        if vector is None or len(vector) != self.dimension:
            raise RuntimeError(
                f"Vector dimension error: expected dim: {self.dimension}, got {len(vector or [])}"
            )

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        return VectorCollectionInfo(
            name=collection_name, exists=self.dimension is not None,
            dense_dimension=self.dimension, points_count=len(self.points),
        )

    async def collection_exists(self, collection_name: str) -> bool:
        return self.dimension is not None

    async def delete_collection(self, collection_name: str) -> None:
        self._require(collection_name)
        self.dimension = None
        self.points.clear()
        self.deletions += 1

    async def create_collection(self, collection_name: str, config: CollectionConfig) -> None:
        if self.dimension is not None:
            raise RuntimeError(f"Collection {collection_name} already exists")
        self.dimension = config.embedding_size

    async def create_index(self, collection_name: str, field_name: str, field_schema: dict) -> None:
        self._require(collection_name)

    async def filter_collection(
        self, must: dict[str, Any] | None = None, should: dict[str, Any] | None = None, **_: object,
    ) -> dict[str, dict[str, Any]]:
        return {"must": dict(must or {}), "should": dict(should or {})}

    def _matching(self, flt: dict[str, dict[str, Any]]) -> list[VectorPoint]:
        def ok(point: VectorPoint) -> bool:
            if not all(_matches_value(_field(point.payload, k), v) for k, v in flt["must"].items()):
                return False
            should = flt["should"]
            return not should or any(_matches_value(_field(point.payload, k), v) for k, v in should.items())

        return [point for point in self.points.values() if ok(point)]

    async def scroll(
        self, collection_name: str, scroll_filter: dict[str, dict[str, Any]], limit: int,
        offset: str | None = None, with_payload: list[str] | None = None,
    ) -> ScrollResult:
        self._require(collection_name)
        matches = self._matching(scroll_filter)
        start = int(offset or 0)
        page = []
        for point in matches[start:start + limit]:
            payload = copy.deepcopy(point.payload)
            if with_payload:
                payload = {}
                for path in with_payload:
                    head, _, rest = path.partition(".")
                    value = _field(point.payload, path)
                    if value is None:
                        continue
                    if rest:
                        payload.setdefault(head, {})[rest] = value
                    else:
                        payload[head] = value
            page.append(VectorPoint(id=point.id, payload=payload))
        end = start + limit
        return ScrollResult(points=page, next_offset=str(end) if end < len(matches) else None)

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        self._require(collection_name)
        return [VectorPoint(id=i, payload=copy.deepcopy(self.points[i].payload)) for i in ids if i in self.points]

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        self._require(collection_name)
        for point in points:
            self._require_dimension(point.dense_vector)
        for point in points:
            self.points[point.id] = VectorPoint(
                id=point.id, dense_vector=list(point.dense_vector or []), payload=copy.deepcopy(point.payload),
            )

    async def update_payload_by_ids(self, collection_name: str, ids: list[str], payload: dict) -> None:
        self._require(collection_name)
        for point_id in ids:
            if point_id in self.points:
                self.points[point_id].payload.update(copy.deepcopy(payload))

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest],
    ) -> list[list[SearchResult]]:
        self._require(collection_name)
        batch = []
        for request in requests:
            self._require_dimension(request.dense_query)
            self.searches.append(request)
            hits = [
                SearchResult(
                    id=point.id,
                    score=sum(a * b for a, b in zip(point.dense_vector or [], request.dense_query or [])),
                    payload=copy.deepcopy(point.payload),
                )
                for point in self._matching(request.filter or {"must": {}, "should": {}})
            ]
            batch.append(sorted(hits, key=lambda hit: -hit.score)[:request.limit])
        return batch
