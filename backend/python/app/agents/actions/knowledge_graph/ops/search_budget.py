"""How much one search shows: its ``response_format`` (C3.22, KN-06).

``concise``, the default, is the search as it always was: a few passages per record within ``search_max_chars``.
``detailed`` doubles the passages a record may show and lets the result grow to the shared tool-result cap. A
result past an engine's cap is kept whole in ``/pipeshub/knowledge`` by the gateway; the loop bounds it as any
other tool result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.modules.retrieval.context.targeted import (
    MAX_PASSAGES_PER_RECORD,
    passages_per_record,
    search_max_chars,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

CONCISE: Final = "concise"
DETAILED: Final = "detailed"
RESPONSE_FORMATS: Final = (CONCISE, DETAILED)
# The run's response_format policy in its tool state (C4.10 ``toolResults.format`` / ``toolResults.formats``): the
# agent's default and the formats the org allows (None: any).
RESPONSE_FORMAT_POLICY: Final = "response_format_policy"


@dataclass(frozen=True, slots=True)
class SearchBudget:
    max_chars: int
    per_record: int


def response_format_error(value: object) -> str | None:
    """The actionable error for a ``response_format`` the search does not know; None when it is one (or unset)."""
    if value is None or value in RESPONSE_FORMATS:
        return None
    return json.dumps({
        "status": "error",
        "message": f"response_format must be one of {', '.join(RESPONSE_FORMATS)}; got {value!r}. Omit it for "
        "the concise default.",
    })


def run_response_format(state: Mapping[str, object] | None, requested: str | None) -> str | None:
    """The format a search runs with under the run's policy: the model's when it names an allowed one, else the
    agent's default, else the first the org allows; unset policy: the model's, concise by default. No state (a
    search before the tool was set up) has no policy, so the search's own guard answers it."""
    policy = state.get(RESPONSE_FORMAT_POLICY) if state else None
    default, allowed = policy if isinstance(policy, tuple) and len(policy) == 2 else (None, None)
    chosen = requested if requested is not None else default
    if allowed and (chosen or CONCISE) not in allowed:
        chosen = default if default in allowed else allowed[0]
    return chosen


def search_budget(response_format: str | None, *, targeted: bool, limit: int, cap: int) -> SearchBudget:
    """The result's size and passages per record (0: no per-record cap) for ``response_format``, within ``cap``
    (the shared tool-result cap)."""
    if not targeted:
        return SearchBudget(cap, 0)
    per_record = passages_per_record(limit)
    if response_format == DETAILED:
        return SearchBudget(cap, min(MAX_PASSAGES_PER_RECORD, per_record * 2))
    return SearchBudget(min(search_max_chars(), cap), per_record)


__all__ = ["CONCISE", "DETAILED", "RESPONSE_FORMATS", "RESPONSE_FORMAT_POLICY", "run_response_format", "SearchBudget", "response_format_error", "search_budget"]
