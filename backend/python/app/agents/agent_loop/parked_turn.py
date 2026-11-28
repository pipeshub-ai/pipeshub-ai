"""The turn that stopped for the person, kept for the request that answers it.

A turn can stop on an approval card (`tool_approvals.py`) or an ask-user question. The answer comes
as a new request, and the history Node sends it has each earlier tool result only as its summary
("Found 4 results", a page's title): the ids the model had looked up are gone, so it looks them up
again, guesses, or can't act on them. So the turn as the model had it is kept (per person and
conversation, until it is answered) and put back in place of its summarised copy, with the tools the
run could see.

A kept turn is used only by its owner, in its conversation, and only when it holds the call being
answered. One that would be too large isn't kept; the answer then uses the history as it is.

It goes back where its copy is, found by its own tool calls: other people may have written after it
(a room), and their rows stay as the history gives them. A part of the turn whose copy the history no
longer carries (removed, or blanked because the person can't read what it used any more) is never
put back.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from pydantic import TypeAdapter

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.utils.error_class import log_failure
from app.utils.logging_fields import log_event

if TYPE_CHECKING:
    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.context.manager import ContextManager
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# How the request that answers an ask-user question starts; it continues the turn, it isn't a question.
ASK_USER_QUESTION_RESUME_PREFIX = "User selections:"
# `context.tool_state`: what a guard refused in this run until the person confirms it on a question card, by kind.
# No lookup answers that question (`hooks/ask_user_question.py`).
CONFIRM_ON_CARD = "ask_user_question_confirms"
# How the answer to an approval card starts (`tool_approvals.TOOL_APPROVAL_RESUME_PREFIX`).
_TOOL_APPROVAL_PREFIX = "Tool approval:"
# How the words that close a turn stopped on an approval card start (`tool_approvals._ask`).
WAITING_PREFIX = "Waiting for your approval to run"
# `context.tool_state`: the calls of the turn this request put back, and from where the run's own messages
# follow (`carried_on`), so `keep` takes that turn and the run's work, never the rows in between.
_CARRIED_ON = "_parked_turn_carried_on"
# A question card expires, and its kept turn is dropped, after this.
QUESTION_TTL_SECONDS = 86400
# `context.tool_state`: tools to make visible again after an answered question (read at PRE_TURN).
VISIBLE_TOOLS = "_parked_turn_visible_tools"
_STOPPED = "_parked_turn_stopped_calls"
_MAX_BYTES = 900_000
# Each result is kept up to the size an approved call's result is given to the model.
_MAX_RESULT_CHARS = 20_000
_MESSAGE = TypeAdapter(Message)


def _path(conversation_id: str, user_id: str | None = None) -> str:
    """One kept turn per person in a conversation. Without `user_id`: the per-conversation key turns
    were kept under before."""
    path = f"/services/agents/parked-turns/{conversation_id}"
    return f"{path}/{user_id}" if user_id else path


def with_expiry(tool_data: Any) -> Any:  # noqa: ANN401
    """A question card as it is sent: with the time its kept turn runs out, after which the card
    shows as expired and the person replies in the chat instead."""
    if not isinstance(tool_data, dict) or "expiresAt" in tool_data:
        return tool_data
    expires = datetime.now(timezone.utc) + timedelta(seconds=QUESTION_TTL_SECONDS)
    return {**tool_data, "expiresAt": expires.isoformat(timespec="seconds").replace("+00:00", "Z")}


def question_index(messages: list[Any]) -> int:
    """Where the person's question is: the last user message that a loop didn't add and that
    doesn't answer an ask-user question. The history keeps those answers as user messages: tapped
    ones start with the resume prefix, and a typed one comes right after a card was shown."""
    ask_calls: set[str] = set()
    card_shown = False
    found = -1
    for i, m in enumerate(messages):
        if isinstance(m, AssistantMessage):
            ask_calls |= {c.id for c in m.tool_calls or [] if "ask_user_question" in (c.name or "")}
        elif isinstance(m, ToolMessage):
            card_shown = card_shown or (m.tool_call_id in ask_calls and not m.is_error)
        elif isinstance(m, UserMessage) and not m.injected:
            if not card_shown and not _text(m.content).lstrip().startswith(ASK_USER_QUESTION_RESUME_PREFIX):
                found = i
            card_shown = False
    return found


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


def closes_a_stopped_turn(message: Message) -> bool:
    """The words a turn stopped on an approval card closed with: an answer with no calls that says it waits."""
    return isinstance(message, AssistantMessage) and not message.tool_calls and WAITING_PREFIX in _text(message.content)


def _call_ids(messages: list[Any]) -> set[str]:
    return {c.id for m in messages if isinstance(m, AssistantMessage) for c in m.tool_calls or [] if c.id}


def _owns(message: Message, ids: set[str]) -> bool:
    if isinstance(message, AssistantMessage):
        return any(c.id in ids for c in message.tool_calls or [])
    return isinstance(message, ToolMessage) and message.tool_call_id in ids


def _sender_ref(context: "AgentContext") -> Optional[str]:
    """The person's name in a room's history (`[participant_1]: ...`); None where the history names nobody."""
    ref = getattr((context.extensions or {}).get("collaboration"), "currentSenderRef", None)
    return ref if isinstance(ref, str) and ref else None


def _is_own_card_answer(message: Message, sender_ref: Optional[str]) -> bool:
    """The person's own answer to a card of the turn (selections, or an approval), which the history
    keeps as a user message between the turn's replies."""
    if not isinstance(message, UserMessage) or message.injected:
        return False
    text = _text(message.content).lstrip()
    if sender_ref is not None and text.startswith("["):
        # As `history.render_user_turn` names the person's own rows; any other name is someone else's.
        named = next((n for n in (f"[{sender_ref}]:", f"[{sender_ref} · private]:") if text.startswith(n)), None)
        if named is None:
            return False
        text = text[len(named):].lstrip()
    return text.startswith((ASK_USER_QUESTION_RESUME_PREFIX, _TOOL_APPROVAL_PREFIX))


def _span(messages: list[Any], ids: set[str], sender_ref: Optional[str]) -> Optional[tuple[int, int]]:
    """Where the copy of the turn's part with calls `ids` is: its first call through its last result,
    and the words that closed it. None when the history doesn't carry it, or when anything but its own
    messages and the person's answers to its cards sits inside: no one else's rows are ever replaced."""
    owned = [i for i, m in enumerate(messages) if _owns(m, ids)]
    if not owned:
        return None
    start, stop = owned[0], owned[-1] + 1
    if any(not _owns(m, ids) and not _is_own_card_answer(m, sender_ref) for m in messages[start:stop]):
        return None
    while stop < len(messages) and closes_a_stopped_turn(messages[stop]):
        stop += 1
    return start, stop


def _own_parts(context: "AgentContext", messages: list[Any]) -> list[list[Any]]:
    """The run's own messages, in the parts the model had them in: from the person's question on, or,
    for a run that carried on a turn this request put back, that turn where it stands and what the run
    added after it. Rows of other people between them are theirs, and are never kept."""
    carried = context.tool_state.get(_CARRIED_ON)
    if not isinstance(carried, dict) or not isinstance(carried.get("from"), int):
        return [messages[question_index(messages) + 1:]]
    ids, added_from = set(carried.get("callIds") or ()), carried["from"]
    parts: list[list[Any]] = []
    part: list[Any] = []
    for message in messages[:added_from]:
        if ids and _owns(message, ids):
            part.append(message)
        elif part:
            parts.append(part)
            part = []
    if part:
        parts.append(part)
    added = list(messages[added_from:])
    return [p for p in (*parts, added) if p]


def _parts_of(turn: list[Any], lengths: object) -> list[list[Any]]:
    """The kept turn split back into its parts; a turn kept whole (before parts were kept) is one."""
    if isinstance(lengths, list) and lengths and all(isinstance(n, int) and n > 0 for n in lengths) and sum(lengths) == len(turn):
        parts, at = [], 0
        for n in lengths:
            parts.append(turn[at:at + n])
            at += n
        return parts
    return [turn]


def carried_on(context: "AgentContext", history_length: int) -> None:
    """The request that answered a card has bound it: the run's own messages follow from here."""
    carried = context.tool_state.setdefault(_CARRIED_ON, {"callIds": []})
    carried["from"] = history_length


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
        parts = [[_kept(m) for m in part] for part in _own_parts(context, messages)]
        turn = [m for part in parts for m in part]
        if not call_id or not _has_call(turn, call_id):
            return
        # A run can stop on an approval card and a question at once: one record answers both.
        stopped = context.tool_state.setdefault(_STOPPED, {})
        stopped[call_id] = ttl_seconds
        record = {
            "orgId": context.org_id, "userId": context.user_id, "callIds": sorted(stopped),
            "messages": [m.model_dump(mode="json") for m in turn],
            "parts": [len(part) for part in parts],
            "visibleTools": sorted(agent.visible_tools or []),
        }
        if len(json.dumps(record, default=str)) > _MAX_BYTES:
            log_event(logger, "parked_turn.not_kept", reason="too_large")
            return
        await context.config_service.set_config(
            _path(context.conversation_id, context.user_id), record, ttl_seconds=max(stopped.values()),
            keep_in_cache=False,
        )
    except Exception as e:
        log_failure(logger, "parked_turn.keep_failed", e)


async def keep_question(context: "AgentContext", agent: "Agent") -> None:
    """Keeps a turn that stopped on an ask-user question."""
    await keep(context, agent, call_id=None, ttl_seconds=QUESTION_TTL_SECONDS)


async def restore(context: "AgentContext", history: "ContextManager", *, call_id: str) -> Optional[list[str]]:
    """Puts the kept turn back in place of its summarised copy, when it is this person's and holds
    `call_id`: each part where its own copy is, everything around them as the history gives it. The
    part with `call_id` must be there, or nothing is put back. Returns the tools the turn could see
    (None when nothing was restored)."""
    if not context.conversation_id or not call_id:
        return None
    try:
        raw = await context.config_service.get_config(
            _path(context.conversation_id, context.user_id), default=None, use_cache=False, keep_in_cache=False,
        )
        if raw is None:
            # shortcut: turns kept before they were kept per person; drop this read one release on.
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
        log_failure(logger, "parked_turn.read_failed", e, include_error_text=False)  # a stored message that fails validation is quoted in the error
        return None
    if not _has_call(turn, call_id):
        return None
    messages = list(await history.messages())
    sender_ref = _sender_ref(context)
    placed: list[tuple[tuple[int, int], list[Any], set[str]]] = []
    for part in _parts_of(turn, raw.get("parts")):
        ids = _call_ids(part)
        span = _span(messages, ids, sender_ref) if ids else None
        if span is not None:
            placed.append((span, part, ids))
    spans = sorted(span for span, _, _ in placed)
    overlapping = any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1))
    if overlapping or not any(call_id in ids for _, _, ids in placed):
        log_event(logger, "parked_turn.not_restored", reason="not_in_history")
        return None
    for (start, stop), part, _ in sorted(placed, key=lambda p: p[0][0], reverse=True):
        messages[start:stop] = part
    await history.clear()
    for message in messages:
        await history.add(message)
    context.tool_state[_CARRIED_ON] = {"callIds": sorted(set().union(*(ids for _, _, ids in placed)))}
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
