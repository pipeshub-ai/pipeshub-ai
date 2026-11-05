"""Unit tests for shared filter-building helpers."""

import pytest

from app.services.vector_db.errors import OrgMismatch, OrgRequired
from app.services.vector_db.filters import (
    build_filter_expression,
    require_filter_org,
)
from app.services.vector_db.models import FieldCondition, FilterExpression, FilterMode
from app.services.vector_db.qdrant.utils import QdrantUtils


class TestBuildFilterExpression:
    def test_must_mode_merges_kwargs(self):
        expr = build_filter_expression(
            FilterMode.MUST,
            must={"orgId": "org1"},
            extra_kwargs={"virtualRecordId": "vr1"},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert len(expr.must) == 2
        keys = {c.key for c in expr.must}
        assert keys == {"metadata.orgId", "metadata.virtualRecordId"}

    def test_should_list_becomes_values(self):
        expr = build_filter_expression(
            FilterMode.SHOULD,
            should={"virtualRecordId": ["a", "b"]},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert len(expr.should) == 1
        assert expr.should[0].values == ["a", "b"]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            build_filter_expression(
                "invalid",
                build_conditions=QdrantUtils.build_conditions_generic,
            )

    def test_blank_values_dropped(self):
        expr = build_filter_expression(
            FilterMode.MUST,
            must={"orgId": "o1", "virtualRecordId": "vr1", "x": " ", "y": None},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert [c.key for c in expr.must] == ["metadata.orgId", "metadata.virtualRecordId"]

    @pytest.mark.parametrize("org", [None, "", "  ", [], [""], [None], ["o1", "o2"], ["o1", ""]])
    @pytest.mark.parametrize("key", ["orgId", "metadata.orgId"])
    def test_a_blank_org_raises(self, key, org) -> None:
        """A blank org would be dropped and the filter would then span every org."""
        with pytest.raises(OrgRequired):
            build_filter_expression(
                FilterMode.MUST, must={key: org, "virtualRecordId": "vr1"},
                build_conditions=QdrantUtils.build_conditions_generic,
            )

    def test_require_filter_org(self) -> None:
        ok = FilterExpression(must=[FieldCondition(key="metadata.orgId", value="o1")])
        require_filter_org(ok)
        require_filter_org(FilterExpression(must=[FieldCondition(key="metadata.orgId", values=["o1"])]))
        for bad in (
            FilterExpression(must=[FieldCondition(key="metadata.virtualRecordId", value="v")]),
            FilterExpression(must=[FieldCondition(key="metadata.orgId", value="")]),
            FilterExpression(should=[FieldCondition(key="metadata.orgId", value="o1")]),
            FilterExpression(must_not=[FieldCondition(key="metadata.orgId", value="o1")]),
            FilterExpression(must=[FieldCondition(key="metadata.orgId", values=["o1", "o2"])]),
            FilterExpression(must=[FieldCondition(key="metadata.orgId", values=["o1", ""])]),
            FilterExpression(must=[FieldCondition(key="metadata.orgId", values=[])]),
        ):
            with pytest.raises(OrgRequired):
                require_filter_org(bad)
        with pytest.raises(OrgMismatch):        # two conditions that disagree
            require_filter_org(FilterExpression(must=[
                FieldCondition(key="metadata.orgId", value="o1"), FieldCondition(key="metadata.orgId", value="o2"),
            ]))

    def test_min_should_match_preserved(self):
        expr = build_filter_expression(
            FilterMode.SHOULD,
            should={"a": 1, "b": 2},
            min_should_match=2,
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert expr.min_should_match == 2

    def test_membership_fields_stay_top_level(self):
        expr = build_filter_expression(
            FilterMode.MUST,
            must={"connectorIds": ["c1"], "recordGroupIds": ["g1"], "orgId": "o1"},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        keys = {c.key for c in expr.must}
        assert keys == {"connectorIds", "recordGroupIds", "metadata.orgId"}


def test_the_scope_keys_are_top_level_fields_like_the_membership_arrays() -> None:
    from app.services.permissions.scope_fields import SCOPE_KEYS
    from app.services.vector_db.filters import (
        build_generic_conditions,
        canonical_filter_key,
    )

    for key in SCOPE_KEYS:
        assert canonical_filter_key(key) == key
        assert [c.key for c in build_generic_conditions({key: "x"})] == [key]
    for key in ("blockId", "virtualRecordId", "orgId"):          # everything else is still a metadata field
        assert canonical_filter_key(key) == f"metadata.{key}"
    assert canonical_filter_key("connectorIds") == "connectorIds"
