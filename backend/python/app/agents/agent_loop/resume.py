"""Recognising a follow-up that answers a card (ask_user_question or a tool approval)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.agent_loop.parked_turn import ASK_USER_QUESTION_RESUME_PREFIX
from app.agents.agent_loop.tool_approvals import (
    is_resume_query as is_tool_approval_resume_query,
)

__all__ = [
    "ASK_USER_QUESTION_RESUME_PREFIX",
    "any_card_shown",
    "card_already_answered",
    "cards_in_request",
    "is_ask_user_question_resume_query",
    "is_open_card_reply",
    "is_typed_card_reply",
    "last_real_user_query",
    "questions_asked_in_request",
    "resolve_card_resume",
]

logger = logging.getLogger(__name__)


def is_ask_user_question_resume_query(text: str | None) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(ASK_USER_QUESTION_RESUME_PREFIX)


def _question_key(text: str) -> str:
    """Two askings of one question differ in case, spacing or the closing mark (as the card compares them)."""
    return re.sub(r"[\s?!.:]+$", "", re.sub(r"\s+", " ", text.lower())).strip()


_ANSWER_LINE = re.compile(r'^\s*\d+\.\s*"(.*)"\s*→', re.MULTILINE)


def _answered_questions(answers: str | None) -> set[str]:
    """The questions a `User selections:` reply answers: one `N. "question" → answer` line each."""
    return {_question_key(q) for q in _ANSWER_LINE.findall(answers or "")}


def _asked_questions(*values: Any) -> set[str]:  # noqa: ANN401
    """Question texts in an ask_user_question call's arguments or result, parsed or as JSON."""
    found: set[str] = set()
    stack = list(values)
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, dict):
            question = value.get("question")
            if isinstance(question, str):
                found.add(_question_key(question))
            stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            stack.extend(value)
    return found


def _turn_asked_questions(turn: dict[str, Any]) -> set[str] | None:
    """What a saved bot turn asked with ask_user_question; None when it asked nothing."""
    rows = [
        row for row in turn.get("tool_results") or []
        if isinstance(row, dict) and "ask_user_question" in str(row.get("tool_name") or "")
    ]
    if not rows:
        return None
    return _asked_questions(*(v for row in rows for v in (row.get("args"), row.get("result"))))


def _turn_showed_card(turn: Any) -> bool:  # noqa: ANN401
    """A saved bot turn that put a question card in front of the user; a failed call showed none."""
    return isinstance(turn, dict) and turn.get("role") == "bot_response" and any(
        isinstance(row, dict)
        and "ask_user_question" in str(row.get("tool_name") or "")
        and row.get("status") != "error"
        for row in turn.get("tool_results") or []
    )


def any_card_shown(previous_conversations: list[dict[str, Any]] | None) -> bool:
    """Whether any saved reply put a question card in front of the user."""
    return any(_turn_showed_card(turn) for turn in previous_conversations or [])


def _is_card_reply(turns: list[dict[str, Any]], index: int) -> bool:
    """A user message that answers a card: tapped answers to a card shown before it, or text typed
    while the card was open. Answers with no card before them were run as a request of their own."""
    content = str(turns[index].get("content") or "")
    if is_ask_user_question_resume_query(content):
        return any_card_shown(turns[:index])
    return index > 0 and _turn_showed_card(turns[index - 1])


def is_open_card_reply(previous_conversations: list[dict[str, Any]] | None, query: str | None) -> bool:
    """Whether `query`, typed rather than tapped, answers the card the last reply ended on."""
    if not isinstance(query, str) or not query.strip():
        return False
    if is_ask_user_question_resume_query(query) or is_tool_approval_resume_query(query):
        return False
    return bool(previous_conversations) and _turn_showed_card(previous_conversations[-1])


def _request_cards(previous_conversations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The cards shown since the person's last request: the one this run answers, and any before it."""
    turns = previous_conversations or []
    cards: list[dict[str, Any]] = []
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.get("role") == "bot_response":
            if _turn_showed_card(turn):
                cards.append(turn)
        elif turn.get("role") == "user_query" and not _is_card_reply(turns, index):
            content = str(turn.get("content") or "")
            if not is_tool_approval_resume_query(content):
                break
    return cards


def cards_in_request(previous_conversations: list[dict[str, Any]] | None) -> int:
    """How many cards the person's current request has shown."""
    return len(_request_cards(previous_conversations))


def questions_asked_in_request(previous_conversations: list[dict[str, Any]] | None) -> set[str]:
    """The questions the current request's cards already put to the user (`_question_key` form).
    A later request may need the same kind of answer again (a second bug, a new project), so only
    the current request's questions are off limits, as a form's slots are scoped to its intent."""
    asked: set[str] = set()
    for turn in _request_cards(previous_conversations):
        asked |= _turn_asked_questions(turn) or set()
    return asked


def card_already_answered(previous_conversations: list[dict[str, Any]] | None, answers: str | None) -> bool:
    """Whether the card these `User selections:` answers are for has already had its reply, so
    running them would repeat that work: a second tab, or a stale one. A failed answer leaves no
    reply (errors are not in the history) and a stopped one did not finish, so either can be
    sent again."""
    if not is_ask_user_question_resume_query(answers):
        return False
    turns = previous_conversations or []
    answered = _answered_questions(answers)
    card_at: int | None = None
    latest_card: int | None = None
    for index, turn in enumerate(turns):
        if not _turn_showed_card(turn):
            continue
        latest_card = index
        if answered & (_turn_asked_questions(turn) or set()):
            card_at = index
    card_at = card_at if card_at is not None else latest_card
    if card_at is None:
        return False
    card_questions = _turn_asked_questions(turns[card_at]) or set()
    replied = False
    for index in range(card_at + 1, len(turns)):
        turn = turns[index]
        if turn.get("role") == "user_query":
            content = str(turn.get("content") or "")
            # Only this card's own answers count, or text typed while it was the open card: the
            # answers to another card, or a later request, leave it unanswered.
            if is_ask_user_question_resume_query(content):
                replied = bool(_answered_questions(content) & card_questions)
            elif not is_tool_approval_resume_query(content):
                replied = index == card_at + 1
        elif turn.get("role") == "bot_response" and replied and turn.get("status") != "stopped" and (
            str(turn.get("content") or "").strip() or _turn_showed_card(turn)
        ):
            return True
    return False


def _authored_by(turns: list[dict[str, Any]], author_ref: str) -> list[dict[str, Any]]:
    """`author_ref`'s questions and the replies put to them (`askedByRef`, else whoever asked the
    question the reply follows), in order."""
    own: list[dict[str, Any]] = []
    asker: str | None = None
    for turn in turns:
        role = turn.get("role")
        if role == "user_query":
            asker = turn.get("authorRef")
            if asker == author_ref:
                own.append(turn)
        elif role == "bot_response" and turn.get("askedByRef", asker) == author_ref:
            own.append(turn)
    return own


def last_real_user_query(
    previous_conversations: list[dict[str, Any]] | None,
    fallback: str,
    *,
    answers: str | None = None,
    author_ref: str | None = None,
) -> str:
    """Original user goal when this request answers a card (ask_user_question or a tool approval).

    `answers` (an ask_user_question reply) picks the user text that opened the card it answers, not
    the latest chat: resuming from a later chat made the model redo that reply and the UI painted it
    onto the card. A card is matched by its question text, so an older card left open while a newer
    one was answered keeps its own goal; without a match the latest card is the one.

    `author_ref` (a shared chat) keeps only that member's questions and the cards put to them, so
    another member's message between a card and its answer neither becomes the goal nor reads as
    an answer to the card.
    """
    turns = previous_conversations or []
    if author_ref is not None:
        turns = _authored_by(turns, author_ref)
    if answers is not None:
        answered = _answered_questions(answers)
        latest_card: str | None = None
        matching_card: str | None = None
        last_user: str | None = None
        for index, turn in enumerate(turns):
            role = turn.get("role")
            content = str(turn.get("content") or "").strip()
            if role == "user_query":
                if content and not _is_card_reply(turns, index) and not is_tool_approval_resume_query(content):
                    last_user = content
            elif role == "bot_response" and last_user:
                asked = _turn_asked_questions(turn)
                if asked is None:
                    continue
                latest_card = last_user
                if answered & asked:
                    matching_card = last_user
        goal = matching_card or latest_card
        if goal:
            return goal
    for index in range(len(turns) - 1, -1, -1):
        turn = turns[index]
        if turn.get("role") != "user_query":
            continue
        content = str(turn.get("content") or "").strip()
        if content and not _is_card_reply(turns, index) and not is_tool_approval_resume_query(content):
            return content
    return fallback


def resolve_card_resume(
    query: str,
    previous_conversations: list[dict[str, Any]] | None,
    *,
    approval_resume: bool = False,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> tuple[str | None, str]:
    """`(answers, goal query)` for a chat with one person. The answers are the tapped selections,
    or the text typed while a card was open (no resume prefix then)."""
    answers = query if is_ask_user_question_resume_query(query) else None
    # Answers with no card in the history to answer (sent by hand, or the card's turn is not in
    # this requester's history) are an ordinary message: resuming would drop their text and
    # carry on an earlier request instead.
    if answers is not None and not any_card_shown(previous_conversations):
        logger.info("ask_card_answers_without_a_card: conversation=%s run=%s", conversation_id, run_id)
        answers = None
    # Typing instead of tapping answers the open card, as when a chat asks in text. A message
    # that carries files answers it too: every later reader of the history counts it so.
    typed = answers is None and is_open_card_reply(previous_conversations, query)
    if typed:
        answers = query
    if answers or approval_resume:
        return answers, last_real_user_query(previous_conversations, query, answers="" if typed else answers)
    return None, query


def is_typed_card_reply(answers: str | None) -> bool:
    """Whether resume answers are text typed at an open card rather than tapped selections."""
    return bool(answers) and not is_ask_user_question_resume_query(answers)
