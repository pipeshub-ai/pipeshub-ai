"""The typed refusal of a resolved-turn header, each with a stable reason (C-6: unique across the harness)."""

from __future__ import annotations

from typing import Final, Literal, get_args

from harness_core.refusals import HarnessRefusal

ResolvedTurnRefusalReason = Literal[
    "resolved_malformed",
    "resolved_algorithm",
    "resolved_unknown_key",
    "resolved_signature",
    "resolved_issuer",
    "resolved_audience",
    "resolved_expired",
    "resolved_subject_mismatch",
    "resolved_agent_mismatch",
    "resolved_conversation_mismatch",
    "resolved_version",
    "resolved_routing",
    "resolved_required",
]
RESOLVED_TURN_REFUSAL_CODES: Final[tuple[ResolvedTurnRefusalReason, ...]] = get_args(ResolvedTurnRefusalReason)
RESOLVED_TURN_REFUSED: Final = "RESOLVED_TURN_REFUSED"


class ResolvedTurnRefused(HarnessRefusal):
    """The request's ``X-PipesHub-Resolved`` is not one this service serves a turn from (HTTP 401)."""

    http_status: int = 401

    def __init__(self, reason: ResolvedTurnRefusalReason) -> None:
        super().__init__(f"the resolved turn was refused: {reason}")
        self.reason_code = reason

    def detail(self) -> dict[str, str]:
        """The response body: the code a caller branches on and the reason, never the token."""
        return {"code": RESOLVED_TURN_REFUSED, "reason": self.reason_code, "message": str(self)}


__all__ = ["RESOLVED_TURN_REFUSAL_CODES", "RESOLVED_TURN_REFUSED", "ResolvedTurnRefusalReason", "ResolvedTurnRefused"]
