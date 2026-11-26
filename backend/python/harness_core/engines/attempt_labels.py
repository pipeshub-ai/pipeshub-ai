"""Which run attempt an engine sandbox serves now, read from its labels (the egress binding's, SIGNFIX).

A turn's own sandbox names its run and attempt as ``runId`` and ``attemptId``. A sandbox kept for a conversation
(SIGNIN-5) cannot: a run's reap filters on ``runId``, and a container's labels are fixed when it is made, so it
would be reaped with the run that made it. It names the attempt it serves under the bound keys instead, set again
on every re-acquire, so its egress binding (and the gateway token issued for it) follows each attempt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

RUN_LABEL = "runId"
ATTEMPT_LABEL = "attemptId"
BOUND_RUN_LABEL = "pipeshub.bound.runId"
BOUND_ATTEMPT_LABEL = "pipeshub.bound.attemptId"


def bound_labels(run_id: str, attempt_id: str) -> dict[str, str]:
    return {BOUND_RUN_LABEL: run_id, BOUND_ATTEMPT_LABEL: attempt_id}


def bound_attempt(labels: Mapping[str, str]) -> tuple[str, str] | None:
    """The (run id, attempt id) the labels name, or ``None`` when they name no whole attempt."""
    for run_key, attempt_key in ((RUN_LABEL, ATTEMPT_LABEL), (BOUND_RUN_LABEL, BOUND_ATTEMPT_LABEL)):
        run_id, attempt_id = labels.get(run_key), labels.get(attempt_key)
        if run_id and attempt_id:
            return run_id, attempt_id
    return None


__all__ = [
    "ATTEMPT_LABEL",
    "BOUND_ATTEMPT_LABEL",
    "BOUND_RUN_LABEL",
    "RUN_LABEL",
    "bound_attempt",
    "bound_labels",
]
