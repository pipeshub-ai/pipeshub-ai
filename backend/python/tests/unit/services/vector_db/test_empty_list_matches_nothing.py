"""An empty list filter value matches nothing; it is never "no filter".

Every provider builds its conditions the same way, and each translates an empty
``values`` into its own match-nothing expression (proved on the real servers in
``tests/integration/vector_db/test_empty_list_matches_nothing.py``).
"""

import pytest
from qdrant_client.http.models import MatchAny

from app.services.vector_db.filters import build_generic_conditions
from app.services.vector_db.models import FieldCondition, FilterExpression
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.services.vector_db.redis.redis_vector import _build_generic_conditions
from app.services.vector_db.redis.utils import filter_expression_to_redis_query

BUILDERS = [
    build_generic_conditions,
    _build_generic_conditions,
    QdrantUtils.build_conditions_generic,
    OpenSearchUtils.build_conditions,
]


@pytest.mark.parametrize("build", BUILDERS)
@pytest.mark.parametrize("empty", [[], [None], ()])
def test_empty_list_keeps_a_condition_with_no_values(build, empty) -> None:
    conds = build({"orgId": "o1", "virtualRecordId": empty})
    assert [(c.key, c.values) for c in conds] == [
        ("metadata.orgId", None),
        ("metadata.virtualRecordId", []),
    ]


@pytest.mark.parametrize("build", BUILDERS)
def test_none_and_blank_are_still_no_condition(build) -> None:
    assert build({"a": None, "b": "  "}) == []


def test_qdrant_translates_to_match_any_of_nothing() -> None:
    flt = QdrantUtils.filter_expression_to_qdrant(
        FilterExpression(must=[FieldCondition(key="metadata.v", values=[])])
    )
    assert flt.must[0].match == MatchAny(any=[])


def test_opensearch_translates_to_empty_terms() -> None:
    q = OpenSearchUtils.filter_expression_to_bool_query(
        FilterExpression(must=[FieldCondition(key="metadata.v", values=[])])
    )
    assert q == {"bool": {"must": [{"terms": {"metadata.v": []}}]}}


def test_redis_translates_to_a_contradiction_not_an_empty_tag_set() -> None:
    q = filter_expression_to_redis_query(
        FilterExpression(must=[FieldCondition(key="metadata.v", values=[])])
    )
    assert "{}" not in q
    assert q == "(@metadata_v:{x} -@metadata_v:{x})"
