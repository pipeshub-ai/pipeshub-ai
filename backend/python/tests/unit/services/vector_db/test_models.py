"""
Unit tests for generic vector DB models (app/services/vector_db/models.py).

Tests cover:
- VectorPoint construction and defaults
- SparseVector construction
- FilterExpression construction, is_empty
- FieldCondition single value vs multi-value
- HybridSearchRequest defaults
- SearchResult construction
- CollectionConfig defaults and custom
- ScrollResult construction
"""

from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    FilterMode,
    FusionMethod,
    HybridSearchRequest,
    ScrollResult,
    SearchResult,
    SparseVector,
    VectorPoint,
)


class TestVectorPoint:
    def test_defaults(self):
        p = VectorPoint(id="abc")
        assert p.id == "abc"
        assert p.dense_vector is None
        assert p.sparse_vector is None
        assert p.payload == {}

    def test_with_vectors(self):
        sv = SparseVector(indices=[0, 5], values=[1.0, 0.5])
        p = VectorPoint(
            id="abc",
            dense_vector=[0.1, 0.2],
            sparse_vector=sv,
            payload={"key": "val"},
        )
        assert p.dense_vector == [0.1, 0.2]
        assert p.sparse_vector.indices == [0, 5]
        assert p.payload == {"key": "val"}


class TestSparseVector:
    def test_construction(self):
        sv = SparseVector(indices=[1, 2, 3], values=[0.1, 0.2, 0.3])
        assert len(sv.indices) == 3
        assert len(sv.values) == 3


class TestFieldCondition:
    def test_single_value(self):
        fc = FieldCondition(key="metadata.orgId", value="org-123")
        assert fc.key == "metadata.orgId"
        assert fc.value == "org-123"
        assert fc.values is None

    def test_multi_value(self):
        fc = FieldCondition(key="metadata.roles", values=["admin", "user"])
        assert fc.values == ["admin", "user"]
        assert fc.value is None


class TestFilterExpression:
    def test_empty(self):
        fe = FilterExpression()
        assert fe.is_empty()
        assert fe.must == []
        assert fe.should == []
        assert fe.must_not == []
        assert fe.min_should_match is None

    def test_not_empty_with_must(self):
        fe = FilterExpression(
            must=[FieldCondition(key="metadata.orgId", value="123")]
        )
        assert not fe.is_empty()

    def test_not_empty_with_should(self):
        fe = FilterExpression(
            should=[FieldCondition(key="metadata.role", values=["admin"])]
        )
        assert not fe.is_empty()

    def test_min_should_match(self):
        fe = FilterExpression(
            should=[
                FieldCondition(key="metadata.a", value="1"),
                FieldCondition(key="metadata.b", value="2"),
            ],
            min_should_match=1,
        )
        assert fe.min_should_match == 1


class TestHybridSearchRequest:
    def test_defaults(self):
        req = HybridSearchRequest()
        assert req.dense_query is None
        assert req.sparse_query is None
        assert req.text_query is None
        assert req.filter is None
        assert req.limit == 10
        assert req.fusion_method == FusionMethod.RRF
        assert req.with_payload is True

    def test_custom(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            text_query="hello world",
            limit=20,
            fusion_method=FusionMethod.ARITHMETIC_MEAN,
        )
        assert req.dense_query == [0.1, 0.2]
        assert req.text_query == "hello world"
        assert req.limit == 20


class TestSearchResult:
    def test_construction(self):
        sr = SearchResult(id="p1", score=0.95, payload={"key": "val"})
        assert sr.id == "p1"
        assert sr.score == 0.95
        assert sr.payload == {"key": "val"}

    def test_defaults(self):
        sr = SearchResult(id="p1", score=0.0)
        assert sr.payload == {}


class TestCollectionConfig:
    def test_defaults(self):
        cc = CollectionConfig()
        assert cc.embedding_size == 1024
        assert cc.distance_metric == DistanceMetric.COSINE
        assert cc.enable_sparse is True
        assert cc.sparse_idf is False

    def test_custom(self):
        cc = CollectionConfig(
            embedding_size=768,
            distance_metric=DistanceMetric.DOT_PRODUCT,
            enable_sparse=False,
        )
        assert cc.embedding_size == 768
        assert cc.distance_metric == DistanceMetric.DOT_PRODUCT
        assert cc.enable_sparse is False


class TestScrollResult:
    def test_construction(self):
        points = [VectorPoint(id="1"), VectorPoint(id="2")]
        sr = ScrollResult(points=points, next_offset="abc")
        assert len(sr.points) == 2
        assert sr.next_offset == "abc"

    def test_defaults(self):
        sr = ScrollResult(points=[])
        assert sr.next_offset is None


class TestEnums:
    def test_filter_mode_values(self):
        assert FilterMode.MUST.value == "must"
        assert FilterMode.SHOULD.value == "should"
        assert FilterMode.MUST_NOT.value == "must_not"

    def test_distance_metric_values(self):
        assert DistanceMetric.COSINE.value == "cosine"
        assert DistanceMetric.L2.value == "l2"
        assert DistanceMetric.DOT_PRODUCT.value == "dot_product"

    def test_fusion_method_values(self):
        assert FusionMethod.RRF.value == "rrf"
        assert FusionMethod.ARITHMETIC_MEAN.value == "arithmetic_mean"
        assert FusionMethod.HARMONIC_MEAN.value == "harmonic_mean"
