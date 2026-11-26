"""The invariant "no ungranted gateway call" as a pure check (P9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.audit.types import (
    ROUTE_NOT_GRANTED,
    RUN_UNKNOWN,
    TOOL_NOT_GRANTED,
    ExecutedCall,
    RunEvidence,
    Violation,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def never(_tool_id: str) -> bool:
    return False


def check_call(
    call: ExecutedCall, evidence: RunEvidence | None, *, outside_grant: Callable[[str], bool] = never
) -> Violation | None:
    """``outside_grant``: tool ids a host serves by other rules than ``grants.tools`` (PipesHub's platform
    tools are allowed by other spec fields); everything else must be in the run's pinned grant."""
    if evidence is None:
        return Violation(RUN_UNKNOWN, call)
    if call.source == "model_gateway":
        return None if call.subject in evidence.grant.route_ids else Violation(ROUTE_NOT_GRANTED, call)
    if call.subject in evidence.grant.tool_ids or outside_grant(call.subject):
        return None
    return Violation(TOOL_NOT_GRANTED, call)


__all__ = ["check_call", "never"]
