"""The turn that stopped for the person, kept for the request that answers it.

A turn can stop on an approval card (`tool_approvals.py`) or an ask-user question. The answer comes
as a new request, and the history Node sends it has each earlier tool result only as its summary
("Found 4 results", a page's title): the ids the model had looked up are gone, so it looks them up
again, guesses, or can't act on them. So the turn as the model had it is kept (per conversation,
until it is answered) and put back in place of its summarised copy, with the tools the run could see.

A kept turn is used only by its owner, in its conversation, and only when it holds the call being
answered. One that would be too large isn't kept; the answer then uses the history as it is.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from pydantic import TypeAdapter

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    TextPart,
    ToolMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.context.manager import ContextManager
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# How the request that answers an ask-user question starts; it continues the turn, it isn't a question.
ASK_USER_QUESTION_RESUME_PREFIX = "User selections:"
# A question has no expiry, but a turn isn't kept longer than this.
QUESTION_TTL_SECONDS = 86400
# `context.tool_state`: tools to make visible again after an answered question (read at PRE_TURN).
VISIBLE_TOOLS = "_parked_turn_visible_tools"
_STOPPED = "_parked_turn_stopped_calls"
_MAX_BYTES = 900_000
# Each result is kept up to the size an approved call's result is given to the model.
_MAX_RESULT_CHARS = 20_000
_MESSAGE = TypeAdapter(Message)


def _path(conversation_id: str) -> str:
    return f"/services/agents/parked-turns/{conversation_id}"


def question_index(messages: list[Any]) -> int:
    """Where the person's question is: the last user message that a loop didn't add and that
    doesn't answer an ask-user question (the history keeps those answers as user messages)."""
    return max(
        (
            i for i, m in enumerate(messages)
            if isinstance(m, UserMessage) and not m.injected
            and not _text(m.content).lstrip().startswith(ASK_USER_QUESTION_RESUME_PREFIX)
        ),
        default=-1,
    )


def ask_call_id(messages: list[Any]) -> str:
    """The last ask-user question call after the person's question, if there is one."""
    for message in reversed(messages[question_index(messages) + 1:]):
        if isinstance(message, AssistantMessage):
            for call in reversed(message.tool_calls or []):
                if "ask_user_question" in (call.name or ""):
                    return call.id
    return ""


def _has_call(messages: list[Any], call_id: str) -> bool:
    return any(isinstance(m, AssistantMessage) and any(c.id == call_id for c in m.tool_calls or []) for m in messages)


def _text(content: Any) -> str:  # noqa: ANN401
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(getattr(part, "text", "")) for part in content if getattr(part, "text", None))
    return json.dumps(content, default=str, ensure_ascii=False)


def _kept(message: Any) -> Any:  # noqa: ANN401
    """A message as it is kept: results cut like an approved call's, no images or thinking."""
    if isinstance(message, ToolMessage):
        text = _text(message.content)
        if len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS] + "\n[result cut]"
        return message.model_copy(update={"content": text})
    if isinstance(message, (AssistantMessage, UserMessage)) and isinstance(message.content, list):
        return message.model_copy(update={"content": [p for p in message.content if isinstance(p, TextPart)]})
    return message


async def keep(context: "AgentContext", agent: "Agent", *, call_id: Optional[str], ttl_seconds: int) -> None:
    """Keeps the turn that stopped on `call_id` (None: on the question it asked last), from the
    person's question on, for its answer. Never raises: the reply it follows is already done."""
    history = agent.context
    if not context.conversation_id or history is None:
        return
    try:
        messages = await history.messages()
        call_id = ask_call_id(messages) if call_id is None else call_id
        turn = [_kept(m) for m in messages[question_index(messages) + 1:]]
        if not call_id or not _has_call(turn, call_id):
            return
        # A run can stop on an approval card and a question at once: one record answers both.
        stopped = context.tool_state.setdefault(_STOPPED, {})
        stopped[call_id] = ttl_seconds
        record = {
            "orgId": context.org_id, "userId": context.user_id, "callIds": sorted(stopped),
            "messages": [m.model_dump(mode="json") for m in turn],
            "visibleTools": sorted(agent.visible_tools or []),
        }
        if len(json.dumps(record, default=str)) > _MAX_BYTES:
            logger.info("The turn waiting for the person is too large to keep; its answer uses the history")
            return
        await context.config_service.set_config(
            _path(context.conversation_id), record, ttl_seconds=max(stopped.values()), keep_in_cache=False,
        )
    except Exception as e:
        logger.warning(f"Couldn't keep the turn waiting for the person: {e}")


async def keep_question(context: "AgentContext", agent: "Agent") -> None:
    """Keeps a turn that stopped on an ask-user question."""
    await keep(context, agent, call_id=None, ttl_seconds=QUESTION_TTL_SECONDS)


async def restore(context: "AgentContext", history: "ContextManager", *, call_id: str) -> Optional[list[str]]:
    """Puts the kept turn back in place of its summarised copy, when it is this person's and holds
    `call_id`. Returns the tools the turn could see (None when nothing was restored)."""
    if not context.conversation_id or not call_id:
        return None
    try:
        raw = await context.config_service.get_config(
            _path(context.conversation_id), default=None, use_cache=False, keep_in_cache=False,
        )
        if (
            not isinstance(raw, dict) or call_id not in (raw.get("callIds") or [])
            or (raw.get("orgId"), raw.get("userId")) != (context.org_id, context.user_id)
            or not isinstance(raw.get("messages"), list)
        ):
            return None
        turn = [_MESSAGE.validate_python(m) for m in raw["messages"]]
    except Exception as e:
        logger.warning(f"Couldn't read the turn waiting for the person: {e}")
        return None
    if not _has_call(turn, call_id):
        return None
    messages = list(await history.messages())
    question = question_index(messages)
    if question < 0:
        return None
    await history.clear()
    for message in [*messages[: question + 1], *turn]:
        await history.add(message)
    names = raw.get("visibleTools")
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


async def restore_question(context: "AgentContext", history: Optional["ContextManager"]) -> None:
    """Before an ask-user answer is bound: the turn that asked, as the model had it. Its tools are
    made visible again at the first turn (`hooks/memory.py`)."""
    if history is None:
        return
    names = await restore(context, history, call_id=ask_call_id(await history.messages()))
    if names:
        context.tool_state[VISIBLE_TOOLS] = names
