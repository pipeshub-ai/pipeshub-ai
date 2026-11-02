import math
import os
from typing import Any, Dict, List

from qdrant_client.http.models import (  # type: ignore
    Batch,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    MinShould,
    Prefetch,
    QuantizationSearchParams,
    QueryRequest,
    SearchParams,
    SparseVector as QdrantSparseVector,
    ValuesCount,
)

from app.services.vector_db.filters import canonical_filter_key
from app.services.vector_db.models import (
    FieldCondition as GenericFieldCondition,
    FilterExpression,
    FilterValue,
    FusionMethod,
    HybridSearchRequest,
    SearchResult,
    SparseVector,
    VectorPoint,
)
from app.utils.logger import create_logger


logger = create_logger("qdrant_utils")

QDRANT_HNSW_EF_ENV = "QDRANT_HNSW_EF"
QDRANT_OVERSAMPLING_ENV = "QDRANT_OVERSAMPLING"
DEFAULT_HNSW_EF = 256
DEFAULT_OVERSAMPLING = 1.5
MAX_HNSW_EF = 4096
MAX_OVERSAMPLING = 4.0
MAX_HNSW_EF_CONSTRUCT = 4096
# RRF gives many results the same score (a dense-only hit and a keyword-only hit
# of the same rank tie). Qdrant orders ties differently on every call, so the
# request asks for a little more than the caller wants and the provider re-sorts
# with a fixed tie-break before cutting.
TIE_MARGIN = 2


def env_number(name: str, default: float, minimum: float, maximum: float, cast: type) -> float:
    """Read a finite number in [minimum, maximum] from the environment, else warn and use ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or not minimum <= value <= maximum:
        logger.warning("%s=%r is not a number in [%s, %s]; using %s", name, raw, minimum, maximum, default)
        return default
    return value


def dense_search_params() -> SearchParams:
    """Query-time knobs for the dense leg, read from the environment on each call.

    ``QDRANT_HNSW_EF`` is the search beam; 0 leaves it to the collection default,
    which is the ``ef_construct`` it was built with. ``QDRANT_OVERSAMPLING`` is
    how many times the requested depth the INT8 scan hands to the full-precision
    rescore; 1 rescores only the requested depth.
    """
    ef = int(env_number(QDRANT_HNSW_EF_ENV, DEFAULT_HNSW_EF, 0, MAX_HNSW_EF, int))
    oversampling = float(env_number(QDRANT_OVERSAMPLING_ENV, DEFAULT_OVERSAMPLING, 1.0, MAX_OVERSAMPLING, float))
    return SearchParams(
        hnsw_ef=ef or None,
        quantization=QuantizationSearchParams(rescore=True, oversampling=oversampling),
    )


class QdrantUtils:
    # ------------------------------------------------------------------
    # Filter builders
    # ------------------------------------------------------------------

    @staticmethod
    def build_conditions(filters: Dict[str, Any]) -> List[FieldCondition]:
        """Build Qdrant-native FieldCondition list from a key→value dict.

        Keys are automatically prefixed with ``metadata.`` except top-level
        membership arrays (``connectorIds``, ``recordGroupIds``).
        """
        conditions: List[FieldCondition] = []
        for key, value in filters.items():
            if value is None:
                continue
            field_key = canonical_filter_key(key)
            if isinstance(value, (list, tuple)):
                filtered = [v for v in value if v is not None]
                if filtered:
                    conditions.append(
                        FieldCondition(
                            key=field_key,
                            match=MatchAny(any=filtered),
                        )
                    )
            elif QdrantUtils._is_valid_value(value):
                conditions.append(
                    FieldCondition(
                        key=field_key,
                        match=MatchValue(value=value),
                    )
                )
        return conditions

    @staticmethod
    def build_conditions_generic(filters: Dict[str, Any]) -> List[GenericFieldCondition]:
        """Build generic FieldCondition list (provider-neutral) from a key→value dict."""
        conditions: List[GenericFieldCondition] = []
        for key, value in filters.items():
            if value is None:
                continue
            field_key = canonical_filter_key(key)
            if isinstance(value, (list, tuple)):
                filtered = [v for v in value if v is not None]
                if filtered:
                    conditions.append(
                        GenericFieldCondition(key=field_key, values=filtered)
                    )
            elif QdrantUtils._is_valid_value(value):
                conditions.append(
                    GenericFieldCondition(key=field_key, value=value)
                )
        return conditions

    @staticmethod
    def _is_valid_value(value: FilterValue) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    @staticmethod
    def filter_expression_to_qdrant(expr: FilterExpression) -> Filter:
        """Convert a generic FilterExpression to a Qdrant Filter."""
        must_conds = [QdrantUtils._generic_to_qdrant(c) for c in expr.must]
        should_conds = [QdrantUtils._generic_to_qdrant(c) for c in expr.should]
        must_not_conds = [QdrantUtils._generic_to_qdrant(c) for c in expr.must_not]

        parts: Dict[str, Any] = {}
        if must_conds:
            parts["must"] = must_conds
        if should_conds:
            parts["should"] = should_conds
            if expr.min_should_match is not None:
                # `min_should_match` is not a Qdrant Filter field — it requires
                # `min_should=MinShould(conditions=..., min_count=n)`.
                # Passing `min_should_match` directly raises a Pydantic ValidationError.
                parts["min_should"] = MinShould(
                    conditions=should_conds,
                    min_count=expr.min_should_match,
                )
        if must_not_conds:
            parts["must_not"] = must_not_conds

        if not parts:
            return Filter(should=[])
        return Filter(**parts)

    @staticmethod
    def _generic_to_qdrant(cond: GenericFieldCondition) -> FieldCondition:
        if cond.values_count_lte is not None:
            # Qdrant expresses an array-length bound natively, alongside an
            # optional match on the same key.
            match = None
            if cond.values is not None:
                match = MatchAny(any=cond.values)
            elif cond.value is not None:
                match = MatchValue(value=cond.value)
            return FieldCondition(
                key=cond.key,
                match=match,
                values_count=ValuesCount(lte=cond.values_count_lte),
            )
        if cond.values is not None:
            return FieldCondition(key=cond.key, match=MatchAny(any=cond.values))
        return FieldCondition(key=cond.key, match=MatchValue(value=cond.value))

    # ------------------------------------------------------------------
    # Point conversion
    # ------------------------------------------------------------------

    @staticmethod
    def vector_points_to_batches(points: list[VectorPoint]) -> list[Batch]:
        """Column-oriented ``Batch`` es for one upsert call: one per vector set.

        ``AsyncQdrantClient.upsert`` walks every value of every
        ``PointStruct`` looking for objects that need server-side inference
        (pure Python, ~2 ms per 1536-dim point, on the caller's event loop);
        a ``Batch`` is checked in well under a millisecond, and the request
        sent is the same. A ``Batch`` names the same vectors for every point,
        so points with different vector names (an image point has no sparse
        vector) go in separate batches. Points are grouped by vector set, not
        by run, so text and image points that alternate still make at most
        one batch per set; order within a set is kept. If an id appears more
        than once, runs are kept instead, so the later write still wins.
        """
        batches: list[Batch] = []

        def flush(group: list[VectorPoint]) -> None:
            if not group:
                return
            vectors: dict[str, Any] = {}
            if group[0].dense_vector is not None:
                vectors["dense"] = [p.dense_vector for p in group]
            if group[0].sparse_vector is not None:
                vectors["sparse"] = [
                    QdrantSparseVector(
                        indices=p.sparse_vector.indices,
                        values=p.sparse_vector.values,
                    )
                    for p in group
                ]
            batches.append(
                Batch(
                    ids=[p.id for p in group],
                    vectors=vectors,
                    payloads=[p.payload for p in group],
                )
            )

        def vector_set(point: VectorPoint) -> tuple[bool, bool]:
            return (point.dense_vector is not None, point.sparse_vector is not None)

        if len({p.id for p in points}) == len(points):
            groups: dict[tuple[bool, bool], list[VectorPoint]] = {}
            for point in points:
                groups.setdefault(vector_set(point), []).append(point)
            for group in groups.values():
                flush(group)
            return batches

        run: list[VectorPoint] = []
        for point in points:
            if run and vector_set(point) != vector_set(run[0]):
                flush(run)
                run = []
            run.append(point)
        flush(run)
        return batches

    # ------------------------------------------------------------------
    # Search request conversion
    # ------------------------------------------------------------------

    @staticmethod
    def search_request_to_qdrant(req: HybridSearchRequest) -> QueryRequest:
        prefetch_list = []
        if req.dense_query is not None:
            prefetch_list.append(
                Prefetch(
                    query=req.dense_query,
                    using="dense",
                    limit=req.limit * 2,
                    params=dense_search_params(),
                )
            )
        if req.sparse_query is not None:
            prefetch_list.append(
                Prefetch(
                    query=QdrantSparseVector(
                        indices=req.sparse_query.indices,
                        values=req.sparse_query.values,
                    ),
                    using="sparse",
                    limit=req.limit * 2,
                )
            )

        qdrant_filter = (
            QdrantUtils.filter_expression_to_qdrant(req.filter)
            if req.filter is not None
            else None
        )

        # Only RRF is supported; default to RRF if anything else is requested
        fusion = Fusion.RRF

        return QueryRequest(
            prefetch=prefetch_list,
            query=FusionQuery(fusion=fusion),
            with_payload=req.with_payload,
            limit=req.limit + TIE_MARGIN,
            filter=qdrant_filter,
        )

    @staticmethod
    def stable_top(points: list[Any], limit: int) -> list[Any]:
        """Order by (-score, id) and keep ``limit``: equal scores come back in the same order every time."""
        return sorted(points, key=lambda p: (-(p.score or 0.0), str(p.id)))[:limit]

    @staticmethod
    def qdrant_result_to_search_result(scored_point: Any) -> SearchResult:
        return SearchResult(
            id=str(scored_point.id),
            score=float(scored_point.score) if scored_point.score is not None else 0.0,
            payload=scored_point.payload or {},
        )

    # ------------------------------------------------------------------
    # Sparse vector conversion (kept for backward compatibility)
    # ------------------------------------------------------------------

    @staticmethod
    def to_generic_sparse_vector(sparse: Any) -> SparseVector:
        """Convert various sparse embedding formats to a generic SparseVector."""
        from app.services.vector_db.models import to_generic_sparse_vector
        return to_generic_sparse_vector(sparse)
