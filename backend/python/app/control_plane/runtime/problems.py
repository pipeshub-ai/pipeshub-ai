"""``Problem`` values whose status and retryability come from the one error catalogue (02-lld §10)."""

from __future__ import annotations

from app.control_plane.contracts.generated.errors import ERROR_CODES, ErrorCode
from app.control_plane.contracts.generated.problem import Problem


def problem(code: ErrorCode, message: str, *, installation: str | None = None) -> Problem:
    entry = ERROR_CODES[code]
    if entry.status is None:
        raise ValueError(f"{code} is never answered as a Problem (the catalogue gives it no status)")
    subject = {"installation": installation} if installation is not None else None
    return Problem.model_validate(
        {"code": code, "status": entry.status.root, "message": message, "retryable": entry.retryable, "subject": subject}
    )


__all__ = ["problem"]
