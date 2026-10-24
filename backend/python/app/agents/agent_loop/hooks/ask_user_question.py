"""`ask_user_question_sse`: POST_TOOL_USE hook reproducing the eager
`ask_user_question` SSE emission from `nodes.py`'s `_execute_sequential`
(lines ~873-891) — emit the structured question payload the moment the tool
result is ready, before any `answer_chunk`/`complete` event, so the frontend
can render interactive option cards and suppress the plain-text answer.

`state["ask_user_question_emitted"]` is the same flag `nodes.py`'s
`_emit_ask_user_question_tool_event` (its end-of-turn fallback emitter, run
before Phase 6's `RespondPipeline` synthesizes the final answer) checks
before emitting again — setting it here means that fallback naturally
no-ops for agent-loop runs exactly as it does for a LangGraph run that hit
the eager path.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.core.scope import StateSlot
from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name
from app.agents.agent_loop.parked_turn import with_expiry
from app.utils.streaming import parse_confidence_from_answer

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.hooks.middleware.context import (
        ModelResponseContext,
        ToolCallContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

_ASK_USER_QUESTION_TOOL_NAMES = frozenset({
    "internaltools__ask_user_question",
    "internaltools_ask_user_question",
    "internaltools.ask_user_question",
})

# Cards one request may show, counting a card shown before any work.
MAX_CARDS_PER_REQUEST = 2

_LIMIT_REASON = (
    "Not asked: this request has already asked {count} times, the most it may. Don't ask again. "
    "Carry on with your best assumption and say in one line what you assumed. If you were about to "
    "change data the user did not ask you to change, don't: tell them what you need instead."
)
_ASKED_BEFORE_REASON = (
    "Not asked: this request already asked {questions}. Use the user's reply (it is in the "
    "conversation), or your best assumption where they skipped it, and carry on. Ask only "
    "questions that have not been asked."
)


def _limit_reason(context: AgentContext, tool_input: dict[str, Any]) -> str | None:
    """Why this question must not be shown: too many cards for one request, or a question this
    request already put to the user. None when it may be asked."""
    from app.agents.agent_loop.factory import (
        _asked_questions,
        cards_in_request,
        questions_asked_in_request,
    )

    # A new request starts its own count and its own questions; an answer carries on the request
    # its card belongs to.
    if not context.tool_state.get("ask_user_question_resume"):
        return None
    history = context.previous_conversations
    shown = cards_in_request(history)
    if shown >= MAX_CARDS_PER_REQUEST:
        return _LIMIT_REASON.format(count=shown)
    repeated = _asked_questions(tool_input.get("questions")) & questions_asked_in_request(history)
    if repeated:
        return _ASKED_BEFORE_REASON.format(questions=", ".join(f'"{q}"' for q in sorted(repeated)))
    return None


def ask_user_question_gate(context: AgentContext) -> "Middleware[ToolCallContext]":
    """PRE_TOOL_USE: deny a question past the request's card limit or one this
    request already asked. A denied call does not end the run, so the model
    carries on with its best assumption."""

    async def _middleware(ctx: ToolCallContext, next_fn: "Next") -> None:
        if resolve_tool_name(ctx) in _ASK_USER_QUESTION_TOOL_NAMES:
            reason = _limit_reason(context, ctx.tool_input or {})
            if reason:
                ctx.deny(reason)
                return
        await next_fn()

    return _middleware


def ask_user_question_sse(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        tool_name = resolve_tool_name(ctx)
        if tool_name not in _ASK_USER_QUESTION_TOOL_NAMES:
            return
        if context.event_sink is None or not context.has_ui_client:
            return

        output = ctx.tool_response
        # A failed call asked the user nothing and does not end the run (the
        # model gets the error and carries on); an event here would make the
        # client drop the answer that follows in favour of a question card
        # it has nothing to render.
        if not output.success:
            return
        raw_result = output.data
        payload: Any = raw_result
        if isinstance(raw_result, str):
            try:
                payload = json.loads(raw_result)
            except (json.JSONDecodeError, TypeError):
                payload = raw_result

        for evt in context.formatter.ask_user_question(
            context, status="success", tool_data=with_expiry(payload),
        ):
            await context.event_sink.write(evt)
        context.tool_state["ask_user_question_emitted"] = True

    return _middleware


# Worded as a request, like completion_gate's nudge: Azure's content filter rejects injected commands.
_ASK_IN_TEXT_NUDGE = (
    "Your reply ends by asking me something in plain text. If you need my answer before you can "
    "finish this request, please ask it with internaltools__ask_user_question instead, so it shows "
    "as a question card (your text reply is then not shown). If the question is the point of your "
    "reply, for example when I asked you to ask me something, please send your reply again as it "
    "was, without mentioning this message."
)

_ASK_IN_TEXT_NUDGED: StateSlot[bool] = StateSlot(
    key="ask_in_text_recovery.nudged", default_factory=lambda: False,
)

# The person asked to be asked: the question in the reply is the answer, not a gap in it.
# Imperative only: "asked me" and "asks me" report something, and "aske" is the typo seen live.
_ASK_ME = re.compile(
    r"\baske?\s+me\b|\b(?:quiz|interview|survey|poll)\s+me\b|\bquestions?\s+for\s+me\b"
    r"|\bsawal\s+pu[ck]h",
    re.IGNORECASE,
)
# "Don't ask me anything, just send it" asks for the opposite.
_NOT_ASKING = re.compile(
    r"\b(?:don['’]?t|do\s+not|never|no\s+need\s+to|stop)\s+(?:\w+\s+){0,2}$",
    re.IGNORECASE,
)


def asked_to_be_asked(query: str | None) -> bool:
    """Whether the request is to be asked something (a question, a quiz), so asking is the task."""
    text = query or ""
    return any(not _NOT_ASKING.search(text[: match.start()]) for match in _ASK_ME.finditer(text))

# The full-width mark closes Chinese and Korean questions.
_QUESTION_END = re.compile(r"[?\uff1f][\s*_`)\]\"'\u201d\u2019]*$")
_LIST_ITEM = re.compile(r"^(?:[-*\u2022+]|\d+[.)])\s+")
# Open invitations, not questions with choices: a card cannot carry them.
_OPEN_HELP = re.compile(
    r"\b(?:how (?:can|may) i (?:help|assist)|what can i (?:help|do)|anything else|is there anything"
    r"|what else (?:can|would|do|should)|let me know if)\b",
    re.IGNORECASE,
)
# A closing sentence that asks the user for input without a question mark: "Please tell me which
# file", "share its title", "upload or paste it". The nudge lets the model keep a text reply when a
# card cannot help, as for a file only the user has.
_INPUT_REQUEST = re.compile(
    r"\b(?:tell me|let me know) (?:which|what|who|where|when|whether)\b"
    r"|\b(?:please|kindly|could you|can you|would you)\b[^.!?]*\b(?:specify|clarify|confirm|choose|pick|select|upload|paste|attach)\b"
    r"|\b(?:share|provide|give|send)\b[^.!?]*\b(?:title|name|link)s?\b",
    re.IGNORECASE,
)
# A follow-up offer after a finished answer: like a chat app, it stays in the text.
_OFFER = re.compile(
    r"^(?:would you like|do you want|want me to|shall i|should i|can i|may i|if you(?:'d| would)? like"
    r"|would it help|need me to)\b",
    re.IGNORECASE,
)
_ALTERNATIVES = re.compile(r"\bor\b", re.IGNORECASE)
# Below this much text before the question, the question is what the reply is.
_SHORT_REPLY_CHARS = 400
# An offer after at least a sentence of answer is a follow-up, even in a short reply.
_ANSWER_BEFORE_OFFER_CHARS = 80


def asks_user_in_text(text: str) -> bool:
    """Whether a final reply, in the end, asks the user for input that a card should carry: it
    closes on a question (perhaps followed by the choices it offers), or a short reply closes on a
    request such as "Please tell me which file", and that is the point of the reply, not an
    optional offer after a finished answer.
    shortcut: a long answer that closes on a question it needs answered (no choice list) is not
    caught; the tool description covers that case. The offer and open-help phrases are English, so
    in other languages a short greeting that ends on a question costs one extra turn."""
    answer, _confidence = parse_confidence_from_answer(text or "")
    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    end = len(lines) - 1
    while end >= 0 and _LIST_ITEM.match(lines[end]) and not _QUESTION_END.search(lines[end]):
        end -= 1
    if end < 0:
        return False
    lists_choices = end < len(lines) - 1
    parts = re.split(r"(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f])\s*", _LIST_ITEM.sub("", lines[end]))
    sentences = [part for part in parts if part]
    closing = sentences[-1].lstrip("*_#> ").strip()
    if _OPEN_HELP.search(closing):
        return False
    before_closing = len(" ".join(lines[:end] + sentences[:-1]))
    if not _QUESTION_END.search(lines[end]):
        return bool(_INPUT_REQUEST.search(closing)) and before_closing < _SHORT_REPLY_CHARS
    # After some answer, a yes/no offer is a follow-up; one that names alternatives ("Q2 or Q3?") is
    # still a clarification unless a full answer came before it.
    if _OFFER.match(closing) and before_closing and (
        before_closing >= _ANSWER_BEFORE_OFFER_CHARS or not _ALTERNATIVES.search(closing)
    ):
        return False
    return before_closing < _SHORT_REPLY_CHARS or lists_choices


def _can_show_card(context: AgentContext, run: "RunScope") -> bool:
    """Only the top-level run of a client that renders cards has the tool; spawned agents lose it."""
    if not context.has_ui_client or run.identity.spawn_depth:
        return False
    registry = run.runtime.tool_registry
    names = set(registry.names()) if registry is not None else set()
    granted = set(run.spec.tool_names) if run.spec.tool_names else names
    return bool(_ASK_USER_QUESTION_TOOL_NAMES & names & granted)


def ask_in_text_recovery(context: AgentContext) -> "Middleware[ModelResponseContext]":
    """POST_MODEL: a reply that asks the user in its text while it could show a card is re-prompted
    once to ask with the tool, or to answer without the question. Same `recovery_message` veto
    `refusal_recovery` uses: the next turn's text, or its card, replaces the reply."""

    async def _middleware(ctx: "ModelResponseContext", next_fn: "Next") -> None:
        await next_fn()

        if ctx.tool_calls or ctx.recovery_message is not None or getattr(ctx.response, "truncated", False):
            return
        run = ctx.scope.run if ctx.scope is not None else None
        if run is None or run.get(_ASK_IN_TEXT_NUDGED) or not _can_show_card(context, run):
            return
        if asked_to_be_asked(context.query):
            return
        # Past the card limit a card would be refused, so the text question stands.
        if _limit_reason(context, {}) is not None:
            return
        text = ctx.response.text if isinstance(ctx.response, AssistantMessage) else ""
        if not asks_user_in_text(text):
            return
        run.set(_ASK_IN_TEXT_NUDGED, True)
        logger.info(
            "ask_in_text_recovery: reply asked the user in text (turn %d); re-prompting once to use the card",
            ctx.turn_index,
        )
        ctx.recovery_message = UserMessage(content=_ASK_IN_TEXT_NUDGE, injected=True)

    return _middleware


__all__ = [
    "ask_in_text_recovery", "ask_user_question_gate", "ask_user_question_sse", "asked_to_be_asked", "asks_user_in_text",
]
