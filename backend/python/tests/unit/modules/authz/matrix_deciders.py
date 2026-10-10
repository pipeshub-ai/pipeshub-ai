from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.modules.authz.role_mapper import rank, to_canonical

CURRENT_PHASE = "PH-03"

Decider = Callable[[dict[str, Any]], dict[str, Any]]

def _role_mapper(row: dict[str, Any]) -> dict[str, Any]:
    resource = row["given"]["resource"]
    roles = [to_canonical(resource, g.get("stored")) for g in row["given"]["grants"]]
    best = max(roles, key=rank, default="none")
    return {"allow": best != "none", "role": best}


# Only rows targeting py need a decider here; rule-engine rows are ts-only.
DECIDERS: dict[str, Decider] = {"roleMapper": _role_mapper}


def phase_number(phase: str) -> int:
    prefix, _, num = phase.partition("-")
    if prefix != "PH" or not num.isdigit():
        raise ValueError(f'Invalid phase "{phase}", expected PH-NN')
    return int(num)


def is_phase_active(since: str, current: str = CURRENT_PHASE) -> bool:
    return phase_number(since) <= phase_number(current)
