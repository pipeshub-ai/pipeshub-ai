"""`artifact_context_reminder`: PRE_TURN hook that surfaces artifacts already
registered for this conversation (from an earlier turn) as a
`goal.constraints` reminder — the SAME injection point
`memory.py::conversation_enrichment` uses for its own "reuse previous
results" nudge, kept as a separate hook rather than folded into that one
since it fires unconditionally (any earlier artifact is worth surfacing),
not only for short "yes"/"do it" follow-ups.

Queries `ArtifactRegistryService` directly (the source of truth) rather
than trying to reconstruct artifact IDs from the bounded, text-only
`previousConversations` tool-result transcript
(`factory.py::_convert_conversation_turn`) — that transcript's `result`
field is a size-capped preview string with no guaranteed parseable
`artifact_id`, while the registry always has the current version/lineage.

Only fires once per run (`turn_index == 0`): conversation history — and
therefore what already exists — doesn't change within a single ReAct run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext
    from app.services.artifact_registry import ArtifactMetadata

logger = logging.getLogger(__name__)

__all__ = ["artifact_context_reminder"]

# Keeps the reminder bounded even for a long-running conversation that has
# accumulated many artifacts — the model rarely needs more than a screenful
# of recent ones to decide whether something already exists.
_MAX_ARTIFACTS_IN_REMINDER = 20


def artifact_context_reminder(context: "AgentContext") -> "Middleware[TurnContext]":
    """PRE_TURN hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: "TurnContext", next_fn: "Next") -> None:
        if ctx.turn_index != 0 or ctx.scope is None or not context.previous_conversations:
            await next_fn()
            return

        registry = context.artifact_registry
        if registry is None or not context.conversation_id:
            await next_fn()
            return

        try:
            from app.services.artifact_registry import Actor

            actor = Actor(org_id=context.org_id, user_id=context.user_id)
            artifacts = await registry.list_for_conversation(
                actor=actor, conversation_id=context.conversation_id, limit=_MAX_ARTIFACTS_IN_REMINDER,
            )
        except Exception:
            logger.warning(
                "Failed to list artifacts for conversation %s", context.conversation_id, exc_info=True,
            )
            await next_fn()
            return

        if artifacts:
            ctx.scope.run.goal.constraints.append(_build_reminder(artifacts))

        await next_fn()

    return _middleware


def _build_reminder(artifacts: list["ArtifactMetadata"]) -> str:
    lines = [
        "Artifacts already exist from earlier in this conversation — reuse them by "
        "artifact_id/name instead of regenerating from scratch when the user asks to "
        "reference, update, or build on one of these:",
    ]
    for a in artifacts:
        parts = [f"artifact_id={a.artifact_id}", f"type={a.artifact_type.value}", f"version={a.version}"]
        if a.source_tool:
            parts.append(f"source_tool={a.source_tool}")
        if a.description:
            parts.append(f"description={a.description!r}")
        if a.derived_from_code_artifact_id:
            parts.append(f"derived_from_code_artifact_id={a.derived_from_code_artifact_id}")
        line = f"- {a.name!r} ({', '.join(parts)})"
        lines.append(line)
    lines.append(
        "To reuse one as input: pass its name in run_code's input_artifacts. To update "
        "one in place: call artifacts__update_artifact(artifact_id=...) if that tool is "
        "available to you, or re-run run_code with input_artifacts=[name] and save the "
        "output under the SAME name. To regenerate an output from its source: find its "
        "derived_from_code_artifact_id, pass THAT code artifact's name into run_code's "
        "input_artifacts, edit it, and re-run."
    )
    return "\n".join(lines)
