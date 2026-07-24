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

To enrich the reminder with tool-call arguments and result summaries
(which the registry does not store), `_extract_conversation_metadata`
cross-references `artifact_id` values from the persisted `tool_results`
entries in `previousConversations` — the same bounded, best-effort data
`_convert_conversation_turn` already reads.  This lets the model see
*how* each artifact was produced (args) and *what* it contains (summary)
without wasting a turn on `retrieve_artifact_content`.

Only fires once per run (`turn_index == 0`): conversation history — and
therefore what already exists — doesn't change within a single ReAct run.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

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

_MAX_ARGS_CHARS = 200


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
            conv_meta = _extract_conversation_metadata(context.previous_conversations)
            ctx.scope.run.goal.constraints.append(_build_reminder(artifacts, conv_meta))

        await next_fn()

    return _middleware


def _extract_conversation_metadata(
    previous_conversations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build ``artifact_id -> {args, summary}`` from persisted tool_results.

    Each ``bot_response`` turn may carry a ``tool_results`` list whose
    entries include ``artifact_id``, ``args``, and ``result_summary`` — the
    same bounded data ``formatPreviousConversations`` (Node.js) already
    serializes from the persisted transcript parts.
    """
    meta: dict[str, dict[str, Any]] = {}
    for turn in previous_conversations:
        for entry in turn.get("tool_results") or []:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("artifact_id")
            if not isinstance(aid, str) or not aid:
                continue
            info: dict[str, Any] = {}
            args = entry.get("args")
            if isinstance(args, dict) and args:
                info["args"] = args
            summary = entry.get("result_summary") or entry.get("result") or ""
            if isinstance(summary, str) and summary:
                info["summary"] = summary
            if info:
                meta[aid] = info
    return meta


def _build_reminder(
    artifacts: list["ArtifactMetadata"],
    conv_meta: dict[str, dict[str, Any]],
) -> str:
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

        extra = conv_meta.get(a.artifact_id, {})
        args = extra.get("args")
        if isinstance(args, dict) and args:
            args_str = json.dumps(args, default=str)
            if len(args_str) > _MAX_ARGS_CHARS:
                args_str = args_str[:_MAX_ARGS_CHARS] + "..."
            parts.append(f"args={args_str}")
        summary = extra.get("summary")
        if isinstance(summary, str) and summary:
            parts.append(f"summary={summary!r}")

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
