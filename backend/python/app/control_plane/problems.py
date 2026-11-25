"""FR-38: a ``Problem`` held to the one catalogue (``contracts/control-plane/errors.json``).

The schema alone accepts any ``CP_*`` code at any status; a reader also needs the code to be catalogued
and its status, retryability and remediation actor to be the catalogue's, or two services could answer
one cause two ways. Node (``control-plane/domain/problem.ts``) and the frontend
(``lib/contracts/control-plane/problem.ts``) read by the same rules, held by
``contracts/control-plane/vectors/problems.json``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from app.control_plane.contracts.generated.errors import ERROR_CODES
from app.control_plane.contracts.generated.problem import Problem

if TYPE_CHECKING:
    from harness_core.contracts.generated.error_catalog import ErrorCatalogEntry

ProblemRefusal = Literal["schema", "unknown_code", "status", "retryable", "remediation"]

_CATALOGUE: dict[str, ErrorCatalogEntry] = {str(code): entry for code, entry in ERROR_CODES.items()}


class ProblemRefused(ValueError):
    """A body that is not the control plane's ``Problem``; ``reason`` is the first rule it breaks."""

    def __init__(self, reason: ProblemRefusal, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: ProblemRefusal = reason


def read_problem(body: object) -> Problem:
    try:
        problem = Problem.model_validate(body)
    except ValidationError as exc:
        raise ProblemRefused("schema", f"{exc.error_count()} schema errors") from None
    entry = _CATALOGUE.get(problem.code)
    if entry is None:
        raise ProblemRefused("unknown_code", problem.code)
    expected = entry.status.root if entry.status is not None else None
    if problem.status != expected:
        raise ProblemRefused("status", f"{problem.code} is {expected}, not {problem.status}")
    if problem.retryable != entry.retryable:
        raise ProblemRefused("retryable", problem.code)
    if problem.remediation is not None and problem.remediation.actor != entry.remediation:
        raise ProblemRefused("remediation", f"{problem.code} is resolved by {entry.remediation}")
    return problem


__all__ = ["ProblemRefusal", "ProblemRefused", "read_problem"]
