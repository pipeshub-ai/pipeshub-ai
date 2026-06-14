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

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
)


def build_filter_expression(
    filter_mode: Union[str, FilterMode] = FilterMode.MUST,
    *,
    must: Optional[Dict[str, FilterValue]] = None,
    should: Optional[Dict[str, FilterValue]] = None,
    must_not: Optional[Dict[str, FilterValue]] = None,
    min_should_match: Optional[int] = None,
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
