from typing import Any, Dict, List

from qdrant_client.http.models import (  # type: ignore
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    MinShould,
    PointStruct,
    Prefetch,
    QueryRequest,
    SparseVector as QdrantSparseVector,
)

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


class QdrantUtils:
    # ------------------------------------------------------------------
    # Filter builders
    # ------------------------------------------------------------------

    @staticmethod
    def build_conditions(filters: Dict[str, Any]) -> List[FieldCondition]:
        """Build Qdrant-native FieldCondition list from a key→value dict.

        Keys are automatically prefixed with ``metadata.``.
        """
        conditions: List[FieldCondition] = []
        for key, value in filters.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                filtered = [v for v in value if v is not None]
                if filtered:
                    conditions.append(
                        FieldCondition(
                            key=f"metadata.{key}",
                            match=MatchAny(any=filtered),
                        )
                    )
            elif QdrantUtils._is_valid_value(value):
                conditions.append(
                    FieldCondition(
                        key=f"metadata.{key}",
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
            if isinstance(value, (list, tuple)):
                filtered = [v for v in value if v is not None]
                if filtered:
                    conditions.append(
                        GenericFieldCondition(key=f"metadata.{key}", values=filtered)
                    )
            elif QdrantUtils._is_valid_value(value):
                conditions.append(
                    GenericFieldCondition(key=f"metadata.{key}", value=value)
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
        if cond.values is not None:
            return FieldCondition(key=cond.key, match=MatchAny(any=cond.values))
        return FieldCondition(key=cond.key, match=MatchValue(value=cond.value))

    # ------------------------------------------------------------------
    # Point conversion
    # ------------------------------------------------------------------

    @staticmethod
    def vector_point_to_qdrant(point: VectorPoint) -> PointStruct:
        vector: Dict[str, Any] = {}
        if point.dense_vector is not None:
            vector["dense"] = point.dense_vector
        if point.sparse_vector is not None:
            vector["sparse"] = QdrantSparseVector(
                indices=point.sparse_vector.indices,
                values=point.sparse_vector.values,
            )
        return PointStruct(id=point.id, vector=vector, payload=point.payload)

    # ------------------------------------------------------------------
    # Search request conversion
    # ------------------------------------------------------------------

    @staticmethod
    def search_request_to_qdrant(req: HybridSearchRequest) -> QueryRequest:
        prefetch_list = []
        if req.dense_query is not None:
            prefetch_list.append(
                Prefetch(query=req.dense_query, using="dense", limit=req.limit * 2)
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
            limit=req.limit,
            filter=qdrant_filter,
        )

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
