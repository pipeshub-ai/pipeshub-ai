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
        """Build generic FieldCondition objects from a filter dictionary."""
        conditions = []
        for key, value in filters.items():
            if value is not None:
                if isinstance(value, (list, tuple)):
                    filtered_list = [v for v in value if v is not None]
                    if filtered_list:
                        conditions.append(FieldCondition(key=f"metadata.{key}", values=filtered_list))
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
                OpenSearchUtils._field_condition_to_clause(c)
                for c in expr.must
            ]

        if expr.should:
            bool_query["should"] = [
                OpenSearchUtils._field_condition_to_clause(c)
                for c in expr.should
            ]
            if expr.min_should_match is not None:
                bool_query["minimum_should_match"] = expr.min_should_match

        if expr.must_not:
            bool_query["must_not"] = [
                OpenSearchUtils._field_condition_to_clause(c)
                for c in expr.must_not
            ]

        if not bool_query:
            return {"match_all": {}}

        return {"bool": bool_query}

    @staticmethod
    def _field_condition_to_clause(cond: FieldCondition) -> Dict[str, Any]:
        """Convert a single FieldCondition to an OpenSearch query clause."""
        if cond.values is not None:
            return {"terms": {cond.key: cond.values}}
        return {"term": {cond.key: cond.value}}

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

    @staticmethod
    def build_hybrid_query(
        request: HybridSearchRequest,
        filter_query: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build an OpenSearch hybrid search query body."""
        queries = []

        # BM25 lexical leg using text_query or page_content match
        if request.text_query:
            queries.append({
                "match": {
                    "page_content": {
                        "query": request.text_query,
                    }
                }
            })
        elif request.sparse_query is not None:
            # If no text_query but sparse_query provided, skip BM25 leg
            # (sparse vectors not natively supported in OpenSearch hybrid query as Qdrant-style)
            pass

        # k-NN dense vector leg
        if request.dense_query is not None:
            queries.append({
                "knn": {
                    "dense_embedding": {
                        "vector": request.dense_query,
                        "k": request.limit,
                    }
                }
            })

        body: Dict[str, Any] = {
            "size": request.limit,
            "_source": {"exclude": ["dense_embedding"]},
        }

        if len(queries) >= 2:
            body["query"] = {"hybrid": {"queries": queries}}
        elif len(queries) == 1:
            body["query"] = queries[0]
        else:
            body["query"] = {"match_all": {}}

        # Apply filter as post_filter for hybrid queries
        if filter_query and filter_query != {"match_all": {}}:
            body["post_filter"] = filter_query

        return body
