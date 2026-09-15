"""Shared filter-building helpers for all vector DB providers.

All three providers (Qdrant, OpenSearch, Redis) share the same
``filter_collection`` argument-parsing logic.  This module extracts that
common body so each provider only overrides the one step that differs
(provider-specific ``build_conditions`` + optional ``min_should_match``
rejection).

Typical provider usage::

    from app.services.vector_db.filters import build_filter_expression

    async def filter_collection(self, filter_mode=..., must=..., ...):
        return build_filter_expression(
            filter_mode,
            must=must, should=should, must_not=must_not,
            min_should_match=min_should_match,
            extra_kwargs=kwargs,
            build_conditions=MyUtils.build_conditions_generic,
        )
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Union

from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
)

# Top-level payload fields — must not be auto-prefixed with ``metadata.``.
TOP_LEVEL_FILTER_FIELDS = frozenset({CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD})


def canonical_filter_key(key: str) -> str:
    """Return the payload path used by vector filters.

    Chunk metadata lives under ``metadata.*``. VRID-level membership arrays
    (``connectorIds``, ``recordGroupIds``) are top-level siblings.
    """
    if key.startswith("metadata.") or key in TOP_LEVEL_FILTER_FIELDS:
        return key
    return f"metadata.{key}"


def build_max_values_conditions(max_values: Dict[str, int]) -> List[FieldCondition]:
    """Build array-length upper-bound conditions.

    Provider-independent: there is no value to match, only a count, so this
    does not go through a provider's ``build_conditions``.
    """
    return [
        FieldCondition(key=canonical_filter_key(key), values_count_lte=limit)
        for key, limit in max_values.items()
        if limit is not None
    ]


def split_values_count_conditions(
    expr: FilterExpression,
) -> tuple[FilterExpression, List[tuple[str, int]]]:
    """Peel array-length conditions off an expression.

    For providers with no server-side count (Redis): query with the returned
    expression, then apply the ``(key, upper_bound)`` pairs to the results. The
    remaining expression always matches a superset, never a subset, so
    post-filtering can only narrow it.
    """
    limits: List[tuple[str, int]] = []

    def _peel(conds: List[FieldCondition], clause: str) -> List[FieldCondition]:
        kept: List[FieldCondition] = []
        for cond in conds:
            if cond.values_count_lte is None:
                kept.append(cond)
                continue
            if clause != "must":
                # Callers apply the peeled bounds conjunctively, so a bound that
                # arrived under should/must_not would come back with its meaning
                # inverted — under must_not it would delete exactly the points it
                # was written to spare.
                raise ValueError(
                    f"values_count_lte on '{cond.key}' is only supported in the "
                    f"MUST clause, not {clause.upper()}"
                )
            limits.append((cond.key, cond.values_count_lte))
            # A condition carrying both a match and a count still has to
            # contribute its match to the query.
            if cond.value is not None or cond.values is not None:
                kept.append(
                    FieldCondition(key=cond.key, value=cond.value, values=cond.values)
                )
        return kept

    stripped = FilterExpression(
        must=_peel(expr.must, "must"),
        should=_peel(expr.should, "should"),
        must_not=_peel(expr.must_not, "must_not"),
        min_should_match=expr.min_should_match,
    )
    return stripped, limits


def build_filter_expression(
    filter_mode: Union[str, FilterMode] = FilterMode.MUST,
    *,
    must: Optional[Dict[str, FilterValue]] = None,
    should: Optional[Dict[str, FilterValue]] = None,
    must_not: Optional[Dict[str, FilterValue]] = None,
    min_should_match: Optional[int] = None,
    max_values: Optional[Dict[str, int]] = None,
    extra_kwargs: Optional[Dict[str, FilterValue]] = None,
    build_conditions: Callable[[Dict[str, FilterValue]], List[FieldCondition]],
) -> FilterExpression:
    """Parse filter_collection arguments and return a ``FilterExpression``.

    Parameters
    ----------
    filter_mode:
        Which clause the positional ``extra_kwargs`` belong to.
    must / should / must_not:
        Explicit per-clause condition dicts (passed as keyword args by caller).
    min_should_match:
        Minimum number of SHOULD clauses that must match.  Providers that do
        not support this should validate before calling this function and raise
        ``NotImplementedError``.
    max_values:
        ``{array_field: upper_bound}`` on how many values the field holds.
        Always added to MUST.
    extra_kwargs:
        Additional ``**kwargs`` forwarded from the caller (routed by filter_mode).
    build_conditions:
        Provider-specific callable that converts ``{key: value}`` dicts into
        ``List[FieldCondition]``.  Called once per non-empty clause dict.
    """
    if isinstance(filter_mode, str):
        try:
            filter_mode = FilterMode(filter_mode.lower())
        except ValueError:
            raise ValueError(
                f"Invalid mode '{filter_mode}'. Must be 'must', 'should', or 'must_not'"
            )

    all_must = dict(must) if must else {}
    all_should = dict(should) if should else {}
    all_must_not = dict(must_not) if must_not else {}

    if extra_kwargs:
        if filter_mode == FilterMode.MUST:
            all_must.update(extra_kwargs)
        elif filter_mode == FilterMode.SHOULD:
            all_should.update(extra_kwargs)
        elif filter_mode == FilterMode.MUST_NOT:
            all_must_not.update(extra_kwargs)

    must_conds = build_conditions(all_must) if all_must else []
    should_conds = build_conditions(all_should) if all_should else []
    must_not_conds = build_conditions(all_must_not) if all_must_not else []

    # Always MUST: an array-length bound narrows a match, it never stands alone
    # as an alternative to one.
    if max_values:
        must_conds = must_conds + build_max_values_conditions(max_values)

    return FilterExpression(
        must=must_conds,
        should=should_conds,
        must_not=must_not_conds,
        min_should_match=min_should_match if should_conds else None,
    )


def is_valid_filter_value(value: FilterValue) -> bool:
    """Return True if the value is a non-empty, non-None filter value."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, list) and not value:
        return False
    return True
