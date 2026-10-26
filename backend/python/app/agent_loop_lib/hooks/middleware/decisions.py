"""Decision enums for gate/observe pipelines, with severity ordering.

Decisions are monotonic (see `agent_loop.hooks.middleware.context`): once a context
escalates to a more severe decision, no later middleware can lower it back
down. This structurally rules out a whole class of bugs where a permissive
middleware registered after a strict one accidentally overrides it (e.g. a
PreToolUse middleware returning "ask" silently overriding an earlier static
"deny").

`PreDecision` is used by every "gate" pipeline that runs BEFORE an action
(PreToolUse, PreAgent, PreTurn, GuardrailInput/Output). `PostDecision` is
used by every "observe" pipeline that runs AFTER an action has already
happened (PostToolUse, PostAgent, PostTurn) and can only suppress/redact the
already-produced result, not prevent it from having run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["PendingApproval", "PreDecision", "PostDecision", "PRE_SEVERITY", "POST_SEVERITY"]


@dataclass(frozen=True)
class PendingApproval:
    """A call PreToolUse middleware leaves for a person to approve on a later turn, instead of
    holding the run open to wait (the HIL flow, which needs a long-lived process). The call
    isn't executed and the model gets `message`; with `end_turn`, the turn ends with `message`
    as its answer, so the person's decision arrives as the next turn."""

    message: str
    end_turn: bool = True
    # What a client needs to ask the person (an id, what would run); carried on the call's
    # TOOL_BLOCKED event as `approval`.
    details: dict[str, Any] = field(default_factory=dict)


class PreDecision(str, Enum):
    """Outcome of a gate pipeline that runs before an action."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PostDecision(str, Enum):
    """Outcome of an observe pipeline that runs after an action."""

    CONTINUE = "continue"
    BLOCK = "block"


# Higher number = more severe / more restrictive. Used to enforce monotonic
# (escalate-only) decision transitions inside the context objects.
PRE_SEVERITY: dict[PreDecision, int] = {
    PreDecision.ALLOW: 0,
    PreDecision.ASK: 1,
    PreDecision.DENY: 2,
}

POST_SEVERITY: dict[PostDecision, int] = {
    PostDecision.CONTINUE: 0,
    PostDecision.BLOCK: 1,
}
