"""EventSubscription management — CRUD for workflow event subscriptions."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from logging import Logger

__all__ = ["SubscriptionService"]


class SubscriptionService:
    """Manages EventSubscription records for workflows.

    Currently a thin stub — subscription persistence is via task trigger
    EVENT kind. Full catalog-validated filter enforcement lands in Phase 4.
    """

    def __init__(self, *, logger: "Logger | None" = None) -> None:
        self._logger = logger or logging.getLogger(__name__)

    def validate_subscription(
        self, event_type: str, filter_predicates: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """Validate filter predicates. Returns list of errors (empty = valid).
        Full implementation in Phase 4 via IEventCatalog.validate_filter."""
        errors: list[dict[str, str]] = []
        for pred in filter_predicates:
            if not pred.get("field"):
                errors.append({"code": "MISSING_FIELD", "field": "", "fix_hint": "Predicate must have a 'field'."})
            if pred.get("op") not in {"eq", "neq", "in", "not_in", "contains", "prefix", "exists", "gt", "lt", "gte", "lte"}:
                errors.append({
                    "code": "INVALID_OP",
                    "field": pred.get("field", ""),
                    "fix_hint": f"Unknown operator '{pred.get('op')}'. Valid: eq, neq, in, not_in, contains, prefix, exists, gt, lt, gte, lte.",
                })
        return errors
