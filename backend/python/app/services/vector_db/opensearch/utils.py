from typing import Any, Dict, List

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FilterValue,
    HybridSearchRequest,
    SearchResult,
    VectorPoint,
)


class OpenSearchUtils:

    @staticmethod
    def build_conditions(filters: Dict[str, Any]) -> List[FieldCondition]:
        """Build generic FieldCondition list from a key→value dict.

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
                        FieldCondition(key=f"metadata.{key}", values=filtered)
                    )
            elif OpenSearchUtils._is_valid_value(value):
                conditions.append(FieldCondition(key=f"metadata.{key}", value=value))
        return conditions

    @staticmethod
    def _is_valid_value(value: FilterValue) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    @staticmethod
    def filter_expression_to_bool_query(expr: FilterExpression) -> Dict[str, Any]:
        """Convert a FilterExpression to an OpenSearch bool query dict."""
        bool_query: Dict[str, Any] = {}

        if expr.must:
            bool_query["must"] = [
                OpenSearchUtils._field_condition_to_clause(c) for c in expr.must
            ]
        if expr.should:
            bool_query["should"] = [
                OpenSearchUtils._field_condition_to_clause(c) for c in expr.should
            ]
            if expr.min_should_match is not None:
                bool_query["minimum_should_match"] = expr.min_should_match
        if expr.must_not:
            bool_query["must_not"] = [
                OpenSearchUtils._field_condition_to_clause(c) for c in expr.must_not
            ]

        return {"bool": bool_query} if bool_query else {"match_all": {}}

    @staticmethod
    def _field_condition_to_clause(cond: FieldCondition) -> Dict[str, Any]:
        if cond.values is not None:
            return {"terms": {cond.key: cond.values}}
        return {"term": {cond.key: cond.value}}

    @staticmethod
    def _wrap_with_filter(
        base_query: Dict[str, Any], filter_query: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Embed the filter inside a bool wrapper that also contains the base query.

        This keeps the filter inside each sub-query so that the k-NN engine
        correctly pre-filters candidates rather than using post_filter (which
        reduces k-NN recall).
        """
        if filter_query and filter_query != {"match_all": {}}:
            return {
                "bool": {
                    "must": [base_query],
                    "filter": [filter_query],
                }
            }
        return base_query

    @staticmethod
    def build_hybrid_query(request: HybridSearchRequest) -> Dict[str, Any]:
        """Build an OpenSearch hybrid query body using the RRF pipeline.

        Filters are placed **inside** each sub-query:
        - BM25 leg: wrapped in ``bool.filter`` to preserve relevance scoring.
        - k-NN leg: placed inside ``knn.<field>.filter`` for **pre-filtering**,
          which ensures the Lucene k-NN engine only scans qualifying documents
          rather than post-filtering the top-k results (which loses recall).

        Per-leg k is raised to ``limit * 2`` so that the RRF merger has enough
        candidates from each leg to produce ``limit`` high-quality final results.
        """
        filter_query: Dict[str, Any] = {}
        if request.filter is not None and not request.filter.is_empty():
            filter_query = OpenSearchUtils.filter_expression_to_bool_query(request.filter)

        per_leg_k = max(request.limit * 2, 20)
        queries: List[Dict[str, Any]] = []

        # BM25 text leg — wrap filter around the match query
        if request.text_query:
            bm25: Dict[str, Any] = {"match": {"page_content": {"query": request.text_query}}}
            queries.append(OpenSearchUtils._wrap_with_filter(bm25, filter_query))

        # Dense k-NN leg — embed filter *inside* the knn clause for pre-filtering
        if request.dense_query is not None:
            knn_params: Dict[str, Any] = {
                "vector": request.dense_query,
                "k": per_leg_k,
            }
            if filter_query and filter_query != {"match_all": {}}:
                knn_params["filter"] = filter_query
            knn: Dict[str, Any] = {"knn": {"dense_embedding": knn_params}}
            queries.append(knn)

        body: Dict[str, Any] = {
            "size": request.limit,
            "_source": {"exclude": ["dense_embedding"]},
        }

        if len(queries) >= 2:
            body["query"] = {"hybrid": {"queries": queries}}
        elif len(queries) == 1:
            body["query"] = queries[0]
        else:
            body["query"] = filter_query if filter_query else {"match_all": {}}

        return body

    @staticmethod
    def vector_point_to_document(point: VectorPoint) -> Dict[str, Any]:
        """Convert a VectorPoint to an OpenSearch document dict."""
        doc: Dict[str, Any] = {
            "metadata": point.payload.get("metadata", {}),
            "page_content": point.payload.get("page_content", ""),
        }
        if point.dense_vector is not None:
            doc["dense_embedding"] = point.dense_vector
        return doc

    @staticmethod
    def hit_to_search_result(hit: Dict[str, Any]) -> SearchResult:
        """Convert an OpenSearch hit to a generic SearchResult."""
        source = hit.get("_source", {})
        raw_score = hit.get("_score")
        return SearchResult(
            id=str(hit.get("_id", "")),
            score=float(raw_score) if raw_score is not None else 0.0,
            payload={
                "metadata": source.get("metadata", {}),
                "page_content": source.get("page_content", ""),
            },
        )
