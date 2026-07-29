"""`ask_user_question_quality` / `log_ask_user_question_quality` /
`ask_outcome_tracking`: observability for `ask_user_question` (Phase 5, Ask
User Tool Improvement Plan) — logs every invocation with enough structure to
later answer "is the model asking too much / too little, and are the
questions any good?" without guessing.

Deliberately separate from `ask_user_question_sse`
(`hooks/ask_user_question.py`): that hook returns early when there is no
`event_sink`/UI client — SSE delivery only matters when someone is watching.
Quality logging must NOT share that gate: a headless/API caller asking a bad
question is exactly as measurable a signal as one with a UI attached.

Two call sites feed the SAME `log_ask_user_question_quality()`:

- Mid-run: `ask_user_question_quality()` below, a POST_TOOL_USE hook
  registered alongside `ask_user_question_sse` in `factory.py`. Reads the
  original call arguments back off `ctx.metadata["_result_accum_args"]`
  (stashed by the already-registered `stash_tool_call_metadata` PRE_TOOL_USE
  hook — see `result_accumulation.py`), because `reasoning`/`user_intent`
  are the tool call's INPUT, not part of its JSON output (`intrim_tools.py`
  deliberately keeps `reasoning` out of the UI-facing return payload — see
  Phase 4).
- Pre-run: `clarification.py::emit_pre_run_clarification()` calls
  `log_ask_user_question_quality()` directly. That path invokes
  `InternalTools.ask_user_question()` as a plain method call — no
  `Agent`/`ToolRegistry` exists yet at that point in the request — so
  POST_TOOL_USE hooks never fire for it (see that function's docstring).

Cross-turn outcome tracking (did the user pick a provided option, or type
something else?) is NOT this hook's job — a POST_TOOL_USE hook runs inside
ONE stateless request and has no visibility into the next one, where the
user's answer actually arrives (see `clarification.py`'s note on PipesHub's
turn-ending HIL model). That is `ask_outcome_tracking()` below: a PRE_TURN
hook that inspects `context.previous_conversations` for the immediately
preceding turn's ask, if any, and compares it against the current turn's
opening message.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext, TurnContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_ASK_USER_QUESTION_TOOL_NAMES = frozenset({
    "internaltools__ask_user_question",
    "internaltools_ask_user_question",
    "internaltools.ask_user_question",
})


def log_ask_user_question_quality(
    context: "AgentContext",
    *,
    ambiguity_type: str,
    user_intent: str,
    question_count: int,
    reasoning: str = "",
) -> None:
    """Shared structured-logging call for both the pre-run and mid-run ask
    paths — see module docstring for why there are two call sites.

    `ambiguity_type` is `"pre_run_moderate"`, `"pre_run_fatal"`, or
    `"mid_run"` today; kept as a free string (not a `Literal`) so a future
    caller can add a label without touching this function's signature.

    `options_grounded` is a heuristic, not a guarantee: True when at least
    one tool already ran earlier in the SAME turn (`all_tool_results`
    non-empty) — on the theory that a question asked after a READ tool call
    more likely used that tool's actual response for its option labels, the
    behavior `intrim_tools.py`'s tool description asks the model to follow
    (see plan weakness #7). Pre-run clarification always logs
    `options_grounded=False` under this heuristic, correctly: no tool has
    run yet at that point in the request.
    """
    options_grounded = bool(context.tool_state.get("all_tool_results"))
    logger.info(
        "ask_user_question_quality: ambiguity_type=%s question_count=%d "
        "options_grounded=%s reasoning_len=%d user_intent_len=%d "
        "org_id=%s conversation_id=%s",
        ambiguity_type, question_count, options_grounded, len(reasoning),
        len(user_intent), context.org_id, context.conversation_id,
    )


def ask_user_question_quality(context: "AgentContext") -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`
    — the mid-run counterpart to `emit_pre_run_clarification()`'s direct call
    above. Logs regardless of `has_ui_client`/`event_sink` (see module
    docstring) and regardless of tool success/failure — a failed
    `ask_user_question` call is itself a signal worth counting."""

    async def _middleware(ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()

        tool_name = resolve_tool_name(ctx)
        if tool_name not in _ASK_USER_QUESTION_TOOL_NAMES:
            return

        # The call's INPUT arguments, stashed by `stash_tool_call_metadata`
        # (already registered as a PRE_TOOL_USE hook in `factory.py`) —
        # `reasoning`/`user_intent` are what the model SENT, not what the
        # tool returned, so the result payload alone can't answer this.
        call_args = ctx.metadata.get("_result_accum_args") or {}
        user_intent = str(call_args.get("user_intent") or "")
        reasoning = str(call_args.get("reasoning") or "")
        question_count = _question_count_from_call_args(call_args)

        log_ask_user_question_quality(
            context,
            ambiguity_type="mid_run",
            user_intent=user_intent,
            question_count=question_count,
            reasoning=reasoning,
        )

    return _middleware


def _question_count_from_call_args(call_args: dict[str, Any]) -> int:
    """Best-effort count of the `questions` the model passed in — accepts
    the same shapes `AskUserQuestionInput`'s coercion validators accept
    (a list, or a JSON-string-encoded list) since these are the RAW call
    arguments, not yet validated/coerced at this point in the pipeline."""
    raw = call_args.get("questions")
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return 0
        return len(parsed) if isinstance(parsed, list) else 0
    return 0


def _last_ask_user_question_option_labels(
    previous_conversations: list[dict[str, Any]],
) -> list[str] | None:
    """Looks only at the MOST RECENT `bot_response` turn (not all of
    history) for an `ask_user_question` tool call and extracts its option
    labels. Returns `None` when that turn didn't ask one, or when its
    `result` — a bounded summary/preview string, not the original
    structured payload, see `factory.py::_convert_conversation_turn` —
    isn't parseable JSON (bounded previews are expected to fail this
    sometimes; this is an observability signal, never something that gates
    the turn)."""
    last_bot_turn: dict[str, Any] | None = None
    for turn in reversed(previous_conversations):
        if turn.get("role") == "bot_response":
            last_bot_turn = turn
            break
    if last_bot_turn is None:
        return None

    for entry in last_bot_turn.get("tool_results") or []:
        if not isinstance(entry, dict) or entry.get("tool_name") not in _ASK_USER_QUESTION_TOOL_NAMES:
            continue
        raw = entry.get("result")
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        labels: list[str] = []
        for question in payload.get("questions") or []:
            if not isinstance(question, dict):
                continue
            for option in question.get("options") or []:
                label = option.get("label") if isinstance(option, dict) else None
                if isinstance(label, str) and label:
                    labels.append(label)
        return labels or None
    return None


def _matches_known_option(user_message: str, labels: list[str]) -> bool:
    normalized = user_message.strip().lower()
    if not normalized:
        return False
    return any(
        normalized == label.strip().lower() or label.strip().lower() in normalized
        for label in labels
    )


def ask_outcome_tracking(context: "AgentContext") -> "Middleware[TurnContext]":
    """PRE_TURN hook (turn 0 only): the cross-turn half of Phase 5 outcome
    measurement — did this turn's opening message match one of the previous
    turn's `ask_user_question` option labels, or did the user type something
    else entirely? See module docstring for why this can't live in the
    POST_TOOL_USE hook above."""

    async def _middleware(ctx: "TurnContext", next_fn: "Next") -> None:
        if ctx.turn_index != 0 or ctx.scope is None or not context.previous_conversations:
            await next_fn()
            return

        labels = _last_ask_user_question_option_labels(context.previous_conversations)
        if labels is not None:
            current_query = ctx.scope.run.goal.description or ""
            picked_known_option = _matches_known_option(current_query, labels)
            logger.info(
                "ask_user_question_outcome: picked_known_option=%s option_count=%d "
                "org_id=%s conversation_id=%s",
                picked_known_option, len(labels), context.org_id, context.conversation_id,
            )

        await next_fn()

    return _middleware


__all__ = [
    "ask_outcome_tracking",
    "ask_user_question_quality",
    "log_ask_user_question_quality",
]
