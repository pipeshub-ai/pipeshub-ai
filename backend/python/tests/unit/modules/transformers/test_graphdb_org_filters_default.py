from app.modules.transformers.graphdb import GraphDBTransformer


def test_name_lookups_carry_no_org_filter_by_default() -> None:
    transformer = GraphDBTransformer.__new__(GraphDBTransformer)

    assert transformer._org_filters("org-1") == {}
