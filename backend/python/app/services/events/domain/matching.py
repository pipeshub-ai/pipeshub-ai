"""FilterPredicate evaluator — pure domain, no I/O.

Evaluates a list of FilterPredicate instances against a normalized AppEvent
payload. All predicates in the list must match (conjunction). 

This is evaluated at fan-out time, before any run is created, so a chatty
app event source with narrow subscriptions costs predicate evaluations not
run spawns.
"""
from __future__ import annotations

from typing import Any

__all__ = ["evaluate_filter", "get_nested", "predicates_from_filter"]

# Fields that identify an entity when a filter names the entity but the
# normalized payload carries an object. Ordered most specific first.
_IDENTITY_FIELDS = ("id", "key", "name", "slug")

_EVENT_TYPE_KEY = "event_type"


def predicates_from_filter(event_filter: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`TaskTrigger.event_filter` -> the predicate list this module evaluates.

    Triggers store a flat mapping (`{"event_type": ..., "channel.id": "C1"}`)
    because that is what `on_event(...)` kwargs and the chat tool produce,
    while the evaluator, the catalog's `validate_filter`, and the UI all speak
    predicates. Converting in one place is what lets a trigger be validated
    against the catalog at creation and evaluated at fire time by the same
    rules.

    A value may also be an explicit `{"op": ..., "value": ...}` mapping, so a
    filter is not limited to equality.
    """
    predicates: list[dict[str, Any]] = []
    for field, value in (event_filter or {}).items():
        if field == _EVENT_TYPE_KEY:
            continue
        if isinstance(value, dict) and "op" in value:
            predicates.append({
                "field": field,
                "op": value.get("op", "eq"),
                "value": value.get("value"),
            })
        else:
            predicates.append({"field": field, "op": "eq", "value": value})
    return predicates


def _comparable_values(actual: Any) -> list[Any]:
    """The values `actual` may legitimately be compared to as a scalar.

    Verifiers normalize provider payloads into objects (`channel: {id, name}`),
    but the documented subscription is `on_event(..., channel="C123")`. Without
    this an equality check compares a dict's repr to "C123" and silently never
    matches -- the failure mode that made event triggers look wired but dead.
    """
    if not isinstance(actual, dict):
        return [actual]
    return [actual[field] for field in _IDENTITY_FIELDS if actual.get(field) is not None]


def get_nested(obj: Any, path: str) -> Any:
    """Get a nested value from a dict using dot-notation path.
    
    e.g. get_nested({"channel": {"id": "C123"}}, "channel.id") → "C123"
    Returns None if any part of the path is missing.
    """
    parts = path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def evaluate_predicate(field: str, op: str, filter_value: Any, payload: dict[str, Any]) -> bool:
    """Evaluate one FilterPredicate against a payload. Returns True if matches."""
    actual = get_nested(payload, field)

    if op == "exists":
        return actual is not None

    if actual is None:
        return False

    candidates = [str(value) for value in _comparable_values(actual)]

    if op == "eq":
        return str(filter_value) in candidates
    elif op == "neq":
        return str(filter_value) not in candidates
    elif op == "in":
        if not isinstance(filter_value, (list, tuple)):
            filter_value = [filter_value]
        wanted = {str(v) for v in filter_value}
        return any(candidate in wanted for candidate in candidates)
    elif op == "not_in":
        if not isinstance(filter_value, (list, tuple)):
            filter_value = [filter_value]
        wanted = {str(v) for v in filter_value}
        return all(candidate not in wanted for candidate in candidates)
    elif op == "contains":
        return any(str(filter_value).lower() in candidate.lower() for candidate in candidates)
    elif op == "prefix":
        return any(candidate.startswith(str(filter_value)) for candidate in candidates)
    elif op == "gt":
        try:
            return float(actual) > float(filter_value)
        except (ValueError, TypeError):
            return False
    elif op == "lt":
        try:
            return float(actual) < float(filter_value)
        except (ValueError, TypeError):
            return False
    elif op == "gte":
        try:
            return float(actual) >= float(filter_value)
        except (ValueError, TypeError):
            return False
    elif op == "lte":
        try:
            return float(actual) <= float(filter_value)
        except (ValueError, TypeError):
            return False
    else:
        # Unknown operator: fail safe (no match)
        return False


def evaluate_filter(predicates: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    """Evaluate a conjunction of filter predicates against a payload.
    
    Returns True if ALL predicates match (empty predicates = match all).
    
    Each predicate is a dict with {field, op, value}.
    """
    for pred in predicates:
        field = pred.get("field", "")
        op = pred.get("op", "eq")
        value = pred.get("value")
        if not evaluate_predicate(field, op, value, payload):
            return False
    return True
