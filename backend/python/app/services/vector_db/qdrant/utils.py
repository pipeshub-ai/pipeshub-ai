import math
import os
from typing import Any, Dict, List

from qdrant_client.http.models import (  # type: ignore
    Batch,
    FieldCondition,
    Filter,
    MatchAny,
    MatchExcept,
    MatchValue,
    MinShould,
    QuantizationSearchParams,
    QueryRequest,
    SearchParams,
    SparseVector as QdrantSparseVector,
    ValuesCount,
)

from app.services.vector_db.filters import build_generic_conditions, canonical_filter_key
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.fusion import rrf
from app.services.vector_db.models import (
    FieldCondition as GenericFieldCondition,
    FilterExpression,
    FilterValue,
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
# Qdrant orders equal scores differently on every call, so each leg asks for a
# little more than its fusion depth and is re-sorted with a fixed tie-break
# before the cut: a tie at the cut-off is not dropped at random.
TIE_MARGIN = 2
# A leg whose margin is all tied with the point at its cut is read again, twice as
# deep each time, up to this many points.
MAX_TIE_READ = 4096


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
                # An empty list matches nothing; Qdrant's MatchAny(any=[]) does.
                conditions.append(
                    FieldCondition(
                        key=field_key,
                        match=MatchAny(any=[v for v in value if v is not None]),
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
        return build_generic_conditions(filters)

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
    def scope_node(node: "sx.Node") -> FieldCondition | Filter:
        """A scope tree (:mod:`~app.services.vector_db.scope_expr`) as a Qdrant condition. An empty ``should`` matches EVERY point in Qdrant, so
        FALSE is a match on an empty list (which matches none), never an empty ``should``."""
        if isinstance(node, sx.Has):
            return FieldCondition(key=node.field, match=MatchAny(any=list(node.values)))
        if isinstance(node, sx.Is):
            return FieldCondition(key=node.field, match=MatchValue(value=node.value))
        if isinstance(node, sx.Within):       # one of the values, and none outside them (MatchExcept on an array: any element outside)
            return Filter(must=[FieldCondition(key=node.field, match=MatchAny(any=list(node.values)))],
                          must_not=[FieldCondition(key=node.field, match=MatchExcept(**{"except": list(node.values)}))])
        if isinstance(node, sx.All):
            return Filter(must=[QdrantUtils.scope_node(p) for p in node.parts]) if node.parts else Filter(must=[])
        if isinstance(node, sx.Some):
            if not node.parts:
                return FieldCondition(key="metadata.orgId", match=MatchAny(any=[]))
            return Filter(should=[QdrantUtils.scope_node(p) for p in node.parts])
        return Filter(must_not=[QdrantUtils.scope_node(node.part)])

    @staticmethod
    def search_request_to_qdrant(req: HybridSearchRequest) -> list[QueryRequest]:
        """The request's legs (dense, then sparse) as Qdrant queries without payloads; fused by ``fuse_legs``."""
        qdrant_filter = (
            QdrantUtils.filter_expression_to_qdrant(req.filter)
            if req.filter is not None
            else None
        )
        if req.scope_filter is not None:
            # ANDed with the request's own filter, never in its place
            sx.check_size(req.scope_filter, "Qdrant")
            scope = QdrantUtils.scope_node(sx.admits(req.scope_filter))
            qdrant_filter = Filter(must=[scope] if qdrant_filter is None else [qdrant_filter, scope])
        # Each leg is searched with the whole filter, as Qdrant applies a fusion
        # query's filter to its prefetches: a leg's candidates are its best
        # ``limit * 2`` after the filter.
        legs: list[QueryRequest] = []
        if req.dense_query is not None:
            legs.append(
                QueryRequest(
                    query=req.dense_query,
                    using="dense",
                    limit=req.limit * 2 + TIE_MARGIN,
                    params=dense_search_params(),
                    filter=qdrant_filter,
                    with_payload=False,
                )
            )
        if req.sparse_query is not None:
            legs.append(
                QueryRequest(
                    query=QdrantSparseVector(
                        indices=req.sparse_query.indices,
                        values=req.sparse_query.values,
                    ),
                    using="sparse",
                    limit=req.limit * 2 + TIE_MARGIN,
                    filter=qdrant_filter,
                    with_payload=False,
                )
            )
        return legs

    @staticmethod
    def fuse_legs(legs: list[list[Any]], limit: int) -> list[tuple[str, float]]:
        """``(id, score)`` of the best ``limit`` points over the legs' answers, fused by RRF.

        Fused here rather than by Qdrant: Qdrant orders equal scores inside a leg
        differently on every call, and a tie that flips moves a point one place in
        that leg and changes its fused score. Each leg keeps Qdrant's order with its
        ties put in id order (``stable_ties``), and is cut to the depth Qdrant fuses
        (``limit * 2``), so the same query gives the same points and scores every
        time. The scores are Qdrant's own: ``1 / (2 + index)`` summed over the legs
        a point is in.
        """
        ranked = [[str(p.id) for p in QdrantUtils.stable_ties(points)[: limit * 2]] for points in legs]
        return rrf(ranked, limit)

    @staticmethod
    def stable_ties(points: list[Any]) -> list[Any]:
        """Qdrant's order, with each run of equal scores put in id order.

        Qdrant answers best first in the collection's own sense (higher for cosine
        and dot, lower for Euclid), so no direction is imposed here: only equal
        scores, whose order Qdrant does not fix, are reordered.
        """
        out: list[Any] = []
        start = 0
        while start < len(points):
            end = start
            while end < len(points) and points[end].score == points[start].score:
                end += 1
            out.extend(sorted(points[start:end], key=lambda p: str(p.id)))
            start = end
        return out

    @staticmethod
    def tie_at_cut(points: list[Any], depth: int, asked: int) -> bool:
        """Whether a leg may hold more points tied with the one at its fusion ``depth``
        than the ``asked`` it returned: it returned all it was asked for, and its last
        point scores the same as the point at the cut. Which of the tied points made it
        into the answer is then Qdrant's choice, so the leg is read deeper."""
        if depth < 1 or len(points) < asked or len(points) < depth:
            return False
        return points[depth - 1].score == points[-1].score

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
