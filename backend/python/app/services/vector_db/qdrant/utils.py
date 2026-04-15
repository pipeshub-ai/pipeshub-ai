from typing import Any, Dict, List, Union

from qdrant_client.http.models import (  # type: ignore
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
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
    @staticmethod
    def build_conditions(filters: Dict[str, Any]) -> List[FieldCondition]:
        """Build list of Qdrant FieldCondition objects from a filter dictionary."""
        conditions = []

        for key, value in filters.items():
            if value is not None:
                if isinstance(value, (list, tuple)):
                    filtered_list = [v for v in value if v is not None]
                    if filtered_list:
                        conditions.append(
                            FieldCondition(
                                key=f"metadata.{key}",
                                match=MatchAny(any=filtered_list)
                            )
                        )
                elif QdrantUtils._is_valid_value(value):
                    conditions.append(
                        FieldCondition(
                            key=f"metadata.{key}",
                            match=MatchValue(value=value)
                        )
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
        must_conditions = []
        should_conditions = []
        must_not_conditions = []

        for cond in expr.must:
            must_conditions.append(QdrantUtils._generic_condition_to_qdrant(cond))

        for cond in expr.should:
            should_conditions.append(QdrantUtils._generic_condition_to_qdrant(cond))

        for cond in expr.must_not:
            must_not_conditions.append(QdrantUtils._generic_condition_to_qdrant(cond))

        filter_parts: Dict[str, Any] = {}
        if must_conditions:
            filter_parts["must"] = must_conditions
        if should_conditions:
            filter_parts["should"] = should_conditions
            if expr.min_should_match is not None:
                filter_parts["min_should_match"] = expr.min_should_match
        if must_not_conditions:
            filter_parts["must_not"] = must_not_conditions

        if not filter_parts:
            return Filter(should=[])

        return Filter(**filter_parts)

    @staticmethod
    def _generic_condition_to_qdrant(cond: GenericFieldCondition) -> FieldCondition:
        """Convert a generic FieldCondition to a Qdrant FieldCondition."""
        if cond.values is not None:
            return FieldCondition(
                key=cond.key,
                match=MatchAny(any=cond.values)
            )
        return FieldCondition(
            key=cond.key,
            match=MatchValue(value=cond.value)
        )

    @staticmethod
    def vector_point_to_qdrant(point: VectorPoint) -> PointStruct:
        """Convert a generic VectorPoint to a Qdrant PointStruct."""
        vector: Dict[str, Any] = {}
        if point.dense_vector is not None:
            vector["dense"] = point.dense_vector
        if point.sparse_vector is not None:
            vector["sparse"] = QdrantSparseVector(
                indices=point.sparse_vector.indices,
                values=point.sparse_vector.values,
            )
        return PointStruct(
            id=point.id,
            vector=vector,
            payload=point.payload,
        )

    @staticmethod
    def search_request_to_qdrant(req: HybridSearchRequest) -> QueryRequest:
        """Convert a generic HybridSearchRequest to a Qdrant QueryRequest."""
        prefetch_list = []

        if req.dense_query is not None:
            prefetch_list.append(
                Prefetch(
                    query=req.dense_query,
                    using="dense",
                    limit=req.limit * 2,
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

        qdrant_filter = None
        if req.filter is not None:
            qdrant_filter = QdrantUtils.filter_expression_to_qdrant(req.filter)

        fusion = Fusion.RRF
        if req.fusion_method == FusionMethod.RRF:
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
        """Convert a Qdrant ScoredPoint to a generic SearchResult."""
        return SearchResult(
            id=str(scored_point.id),
            score=float(scored_point.score) if scored_point.score is not None else 0.0,
            payload=scored_point.payload or {},
        )

    @staticmethod
    def to_generic_sparse_vector(sparse: Any) -> SparseVector:
        """Convert various sparse embedding formats to a generic SparseVector."""
        if isinstance(sparse, SparseVector):
            return sparse
        if hasattr(sparse, "indices") and hasattr(sparse, "values"):
            return SparseVector(indices=list(sparse.indices), values=list(sparse.values))
        if isinstance(sparse, dict) and "indices" in sparse and "values" in sparse:
            return SparseVector(indices=sparse["indices"], values=sparse["values"])
        raise ValueError("Cannot convert sparse embedding to SparseVector")
