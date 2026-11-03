"""Which node a record is expected to inherit permissions from.

A record with no parent record inherits from its record group. A nested one
inherits from the record above it, so a restriction part way down a tree is
not bypassed; only a synced parent of another record group sends it back to
its own group.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from validation.graph_edge_validator import build_record_edge_expectations

pytestmark = pytest.mark.unit

CONNECTOR = "c1"


def _record(*, parent: str | None = None, inherit: bool = True, group: str = "g1") -> SimpleNamespace:
    return SimpleNamespace(
        id="r1", record_type="TICKET", external_record_group_id="ext-g1", record_group_id=group,
        parent_external_record_id=parent, inherit_permissions=inherit, is_dependent_node=False,
    )


def _parent(*, group: str = "g1", placeholder: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id="p1", record_group_id=group, is_placeholder=placeholder)


def _inherits(record: SimpleNamespace, parent: SimpleNamespace | None) -> dict[str, str]:
    """``{"record_group" | "record": cardinality}`` of the inheritance expectations."""
    return {
        exp.to_ref.kind: exp.cardinality
        for exp in build_record_edge_expectations(record, CONNECTOR, parent=parent)
        if exp.collection == "inheritPermissions"
    }


def test_a_record_with_no_parent_inherits_from_its_group() -> None:
    assert _inherits(_record(), None) == {"record_group": "exactly_one"}


def test_a_nested_record_inherits_from_its_parent_and_not_from_the_group() -> None:
    assert _inherits(_record(parent="ext-p1"), _parent()) == {
        "record_group": "none", "record": "exactly_one",
    }


def test_a_parent_of_another_group_sends_the_record_back_to_its_own_group() -> None:
    assert _inherits(_record(parent="ext-p1"), _parent(group="g2")) == {
        "record_group": "exactly_one", "record": "none",
    }


def test_a_placeholder_parent_of_another_group_still_holds_the_record() -> None:
    assert _inherits(_record(parent="ext-p1"), _parent(group="g2", placeholder=True)) == {
        "record_group": "none", "record": "exactly_one",
    }


def test_a_record_that_does_not_inherit_has_neither_edge() -> None:
    assert _inherits(_record(parent="ext-p1", inherit=False), _parent()) == {
        "record_group": "none", "record": "none",
    }
