"""An in-memory ``IVectorDBService`` for tests.

It keeps the contract the real providers share, so code under test sees the
same behaviour it would on Qdrant, OpenSearch or Redis, without a server:

- filters: MUST all, MUST_NOT none, SHOULD at least ``min_should_match`` (1
  by default); a list field matches when any element matches; a
  ``values_count_lte`` bound passes an absent field;
- ``retrieve_points`` and ``update_payload_by_ids`` work by id and ignore ids
  with no point; ``set_payload`` and ``delete_points`` refuse an empty filter;
- ``scroll`` pages in id order with an id cursor;
- search fuses a dense cosine ranking and a lexical one (term overlap with
  ``page_content``) by reciprocal rank, like the server-side text search
  providers, so scores are rank-fused, not similarities.

``tests/unit/modules/transformers/test_entity_vectorstore_contract.py`` runs
the real-backend entity store suite against it, which keeps the fake honest.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from app.services.vector_db.filters import build_filter_expression, canonical_filter_key
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
    HealthStatus,
    HybridSearchRequest,
    ScoreSemantics,
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)

_RRF_K = 60
_TOKEN = re.compile(r"\w+")
_ABSENT = object()


def _conditions(filters: dict[str, FilterValue]) -> list[FieldCondition]:
    conditions = []
    for key, value in filters.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            values = [v for v in value if v is not None]
            if values:
                conditions.append(FieldCondition(key=canonical_filter_key(key), values=values))
        else:
            conditions.append(FieldCondition(key=canonical_filter_key(key), value=value))
    return conditions


def _lookup(payload: dict[str, Any], dotted: str) -> object:
    node: object = payload
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return _ABSENT
        node = node[part]
    return node


def _matches(payload: dict[str, Any], condition: FieldCondition) -> bool:
    found = _lookup(payload, condition.key)
    if condition.values_count_lte is not None:
        # A single value counts as one, as every provider counts it.
        count_ok = found is _ABSENT or found is None or (
            (len(found) if isinstance(found, list) else 1) <= condition.values_count_lte
        )
        if not count_ok:
            return False
        if condition.value is None and condition.values is None:
            return True
    if found is _ABSENT or found is None:
        return False
    wanted = condition.values if condition.values is not None else [condition.value]
    present = found if isinstance(found, list) else [found]
    return any(item in wanted for item in present)


def _passes(payload: dict[str, Any], expr: FilterExpression | None) -> bool:
    if expr is None:
        return True
    if not all(_matches(payload, c) for c in expr.must):
        return False
    if any(_matches(payload, c) for c in expr.must_not):
        return False
    if expr.should:
        # Qdrant's reading: at least one SHOULD must match even beside MUST.
        # OpenSearch makes SHOULD optional with min_should_match=0 and Redis
        # rejects the argument, so this is the strictest common behaviour.
        needed = expr.min_should_match or 1
        if sum(_matches(payload, c) for c in expr.should) < needed:
            return False
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.casefold()))


class InMemoryVectorDBService(IVectorDBService):
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, VectorPoint]] = {}
        self.configs: dict[str, CollectionConfig] = {}

    # ---- lifecycle and identity --------------------------------------

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def get_service_name(self) -> str:
        return "memory"

    def get_service(self) -> IVectorDBService:
        return self

    def get_service_client(self) -> object:
        return self

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities(
            supports_sparse_vectors=False,
            supports_server_side_text_search=True,
            score_semantics=ScoreSemantics.RANK_FUSED,
        )

    async def health_check(self) -> VectorDBHealth:
        return VectorDBHealth(status=HealthStatus.HEALTHY)

    # ---- collections -------------------------------------------------

    async def create_collection(
        self, collection_name: str = "records", config: CollectionConfig | None = None,
    ) -> None:
        self.collections.setdefault(collection_name, {})
        self.configs[collection_name] = config or CollectionConfig()

    async def get_collections(self) -> object:
        return list(self.collections)

    async def get_collection(self, collection_name: str) -> object:
        return self.collections.get(collection_name)

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        if collection_name not in self.collections:
            return VectorCollectionInfo(name=collection_name, exists=False)
        return VectorCollectionInfo(
            name=collection_name,
            exists=True,
            dense_dimension=self.configs[collection_name].embedding_size,
            points_count=len(self.collections[collection_name]),
        )

    async def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    async def delete_collection(self, collection_name: str) -> None:
        self.collections.pop(collection_name, None)
        self.configs.pop(collection_name, None)

    async def create_index(self, collection_name: str, field_name: str, field_schema: dict) -> None:
        if collection_name not in self.collections:
            raise ValueError(f"no collection {collection_name!r}")

    # ---- filters -----------------------------------------------------

    async def filter_collection(
        self,
        filter_mode: str | FilterMode = FilterMode.MUST,
        must: dict[str, FilterValue] | None = None,
        should: dict[str, FilterValue] | None = None,
        must_not: dict[str, FilterValue] | None = None,
        min_should_match: int | None = None,
        max_values: dict[str, int] | None = None,
        **filters: FilterValue,
    ) -> FilterExpression:
        return build_filter_expression(
            filter_mode,
            must=must, should=should, must_not=must_not,
            min_should_match=min_should_match, max_values=max_values,
            extra_kwargs=filters, build_conditions=_conditions,
        )

    # ---- data --------------------------------------------------------

    def _points(self, collection_name: str) -> dict[str, VectorPoint]:
        if collection_name not in self.collections:
            raise ValueError(f"no collection {collection_name!r}")
        return self.collections[collection_name]

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: str | None = None,
        with_payload: list[str] | None = None,
    ) -> ScrollResult:
        matching = sorted(
            (p for p in self._points(collection_name).values() if _passes(p.payload, scroll_filter)),
            key=lambda p: p.id,
        )
        if offset is not None:
            matching = [p for p in matching if p.id >= offset]
        page = matching[: max(0, limit)]
        next_offset = matching[limit].id if len(matching) > limit else None
        return ScrollResult(
            points=[VectorPoint(id=p.id, payload=copy.deepcopy(p.payload)) for p in page],
            next_offset=next_offset,
        )

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        points = self._points(collection_name)
        return [
            VectorPoint(id=i, payload=copy.deepcopy(points[i].payload)) for i in ids if i in points
        ]

    async def update_payload_by_ids(self, collection_name: str, point_ids: list[str], payload: dict) -> None:
        points = self._points(collection_name)
        for point_id in point_ids:
            if point_id in points:
                points[point_id].payload.update(copy.deepcopy(payload))

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest],
    ) -> list[list[SearchResult]]:
        return [self._search(collection_name, request) for request in requests]

    def _search(self, collection_name: str, request: HybridSearchRequest) -> list[SearchResult]:
        candidates = [
            p for p in self._points(collection_name).values() if _passes(p.payload, request.filter)
        ]
        fused: dict[str, float] = {}
        if request.dense_query is not None:
            ranked = sorted(
                (p for p in candidates if p.dense_vector is not None),
                key=lambda p: (-_cosine(request.dense_query, p.dense_vector), p.id),
            )
            for rank, point in enumerate(ranked, start=1):
                fused[point.id] = fused.get(point.id, 0.0) + 1.0 / (_RRF_K + rank)
        if request.text_query:
            query_terms = _tokens(request.text_query)
            scored = [
                (len(query_terms & _tokens(str(p.payload.get("page_content") or ""))), p)
                for p in candidates
            ]
            ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1].id))
            for rank, (_, point) in enumerate(ranked, start=1):
                fused[point.id] = fused.get(point.id, 0.0) + 1.0 / (_RRF_K + rank)
        by_id = {p.id: p for p in candidates}
        best = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[: request.limit]
        return [
            SearchResult(
                id=point_id,
                score=score,
                payload=copy.deepcopy(by_id[point_id].payload) if request.with_payload else {},
            )
            for point_id, score in best
        ]

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        stored = self._points(collection_name)
        for point in points:
            stored[point.id] = copy.deepcopy(point)

    async def delete_points(
        self, collection_name: str, filter: FilterExpression, refresh: bool = False,  # noqa: A002 - interface name
    ) -> None:
        if filter.is_empty():
            raise ValueError("refusing to delete with an empty filter")
        if not filter.has_positive_match():
            # A length bound alone also matches every point without the field.
            raise ValueError("refusing to delete with a filter that matches no value")
        stored = self._points(collection_name)
        for point_id in [i for i, p in stored.items() if _passes(p.payload, filter)]:
            del stored[point_id]

    async def overwrite_payload(self, collection_name: str, payload: dict, points: FilterExpression) -> None:
        if points.is_empty():
            raise ValueError("refusing to overwrite payload with an empty filter")
        for point in self._points(collection_name).values():
            if _passes(point.payload, points):
                point.payload = copy.deepcopy(payload)

    async def set_payload(
        self, collection_name: str, payload: dict, filter: FilterExpression, refresh: bool = False,  # noqa: A002
    ) -> None:
        if filter.is_empty():
            raise ValueError("refusing to set payload with an empty filter")
        for point in self._points(collection_name).values():
            if _passes(point.payload, filter):
                point.payload.update(copy.deepcopy(payload))
