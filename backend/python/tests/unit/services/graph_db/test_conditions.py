"""``Cond``: explicit operators, never an overloaded ``None``."""

import pytest

from app.services.graph_db.conditions import Cond, check_conditions, holds

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("cond", "doc", "expected"), [
    (Cond("v", "eq", 3), {"v": 3}, True),
    (Cond("v", "eq", 3), {"v": 4}, False),
    (Cond("v", "eq", 3), {}, False),
    (Cond("v", "eq", 3), {"v": None}, False),
    (Cond("v", "is_null"), {}, True),
    (Cond("v", "is_null"), {"v": None}, True),
    (Cond("v", "is_null"), {"v": 0}, False),
    (Cond("v", "null_or_eq", 3), {}, True),
    (Cond("v", "null_or_eq", 3), {"v": 3}, True),
    (Cond("v", "null_or_eq", 3), {"v": 4}, False),
    (Cond("v", "null_or_le", 5), {}, True),
    (Cond("v", "null_or_le", 5), {"v": 5}, True),
    (Cond("v", "null_or_le", 5), {"v": 4}, True),
    (Cond("v", "null_or_le", 5), {"v": 6}, False),
    (Cond("v", "null_or_le", 5), {"v": 0}, True),
    (Cond("v", "null_or_le", 5), {"v": "a"}, False),
    (Cond("v", "null_or_le", 5), {"v": True}, False),
])
def test_holds(cond: Cond, doc: dict, expected: bool) -> None:
    assert holds(cond, doc) is expected


@pytest.mark.parametrize("make", [
    lambda: Cond("v", "eq", None),
    lambda: Cond("v", "null_or_le", None),
    lambda: Cond("v", "null_or_eq", None),
    lambda: Cond("v", "eq", True),
    lambda: Cond("v", "is_null", 1),
    lambda: Cond("v", "gt", 1),
    lambda: Cond("", "eq", 1),
    lambda: Cond("v", "null_or_le", "x"),
])
def test_a_condition_cannot_be_ambiguous(make) -> None:
    with pytest.raises(ValueError):
        make()


def test_a_write_needs_a_condition() -> None:
    with pytest.raises(ValueError):
        check_conditions([])
