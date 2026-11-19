"""Conditions of a conditional node write (``update_node_fields_if``).

``update_node_fields_if_match`` can say "this field equals that value" and,
with ``None``, "this field is absent". A write that must also pass while a field
is absent *or* not newer than a value ("``propsSrc`` is unset or at most the
stamp I hold") needs more words, and overloading ``None`` for them would make
every existing caller's meaning depend on the operator. So each condition names
its operator, and ``None`` is never a value here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EQ = "eq"
IS_NULL = "is_null"
NULL_OR_EQ = "null_or_eq"
NULL_OR_LE = "null_or_le"

#: Operators that compare against a value; ``is_null`` takes none.
VALUE_OPS = frozenset({EQ, NULL_OR_EQ, NULL_OR_LE})
OPS = VALUE_OPS | {IS_NULL}


@dataclass(frozen=True)
class Cond:
    """One condition on a stored field: ``field`` ``op`` ``value``."""

    field: str
    op: str
    value: Any = None

    def __post_init__(self) -> None:
        if not self.field or not isinstance(self.field, str):
            raise ValueError("a condition names a field")
        if self.op not in OPS:
            raise ValueError(f"unknown condition operator {self.op!r}")
        if self.op == IS_NULL:
            if self.value is not None:
                raise ValueError("is_null takes no value")
        elif self.value is None or isinstance(self.value, bool):
            # None would read as "absent" and a bool compares as an int: neither is a stamp or a counter.
            raise ValueError(f"{self.op} needs a value that is not None or a bool")
        if self.op == NULL_OR_LE and not isinstance(self.value, (int, float)):
            raise ValueError("null_or_le compares numbers")


def check_conditions(conditions: Sequence[Cond]) -> None:
    """Raise ``ValueError`` for a write with no condition (use the unconditional update)."""
    if not conditions:
        raise ValueError("update_node_fields_if needs a condition")
    for cond in conditions:
        if not isinstance(cond, Cond):
            raise ValueError("conditions are Cond instances")


def holds(cond: Cond, document: Mapping[str, Any]) -> bool:
    """Whether ``document`` satisfies ``cond``. The reference the two providers' statements implement."""
    current = document.get(cond.field)
    if cond.op == IS_NULL:
        return current is None
    if cond.op == EQ:
        return current is not None and current == cond.value
    if cond.op == NULL_OR_EQ:
        return current is None or current == cond.value
    # NULL_OR_LE: a non-number never passes (a stray string is not "older").
    if current is None:
        return True
    return isinstance(current, (int, float)) and not isinstance(current, bool) and current <= cond.value
