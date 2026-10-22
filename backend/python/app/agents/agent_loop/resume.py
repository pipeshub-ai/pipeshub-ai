"""Recognising a follow-up that answers a card (ask_user_question or a tool approval)."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.agent_loop.parked_turn import ASK_USER_QUESTION_RESUME_PREFIX
from app.agents.agent_loop.tool_approvals import (
    is_resume_query as is_tool_approval_resume_query,
)

__all__ = ["ASK_USER_QUESTION_RESUME_PREFIX", "is_ask_user_question_resume_query", "last_real_user_query"]


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


def last_real_user_query(
    previous_conversations: list[dict[str, Any]] | None,
    fallback: str,
    *,
    answers: str | None = None,
) -> str:
    """Original user goal when this request answers a card (ask_user_question or a tool approval).

    `answers` (an ask_user_question reply) picks the user text that opened the card it answers, not
    the latest chat: resuming from a later chat made the model redo that reply and the UI painted it
    onto the card. A card is matched by its question text, so an older card left open while a newer
    one was answered keeps its own goal; without a match the latest card is the one.
    """
    if answers is not None:
        answered = _answered_questions(answers)
        latest_card: str | None = None
        matching_card: str | None = None
        last_user: str | None = None
        for turn in previous_conversations or []:
            role = turn.get("role")
            content = str(turn.get("content") or "").strip()
            if role == "user_query":
                if content and not is_ask_user_question_resume_query(content) and not is_tool_approval_resume_query(content):
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
    for turn in reversed(previous_conversations or []):
        if turn.get("role") != "user_query":
            continue
        content = str(turn.get("content") or "").strip()
        if content and not is_ask_user_question_resume_query(content) and not is_tool_approval_resume_query(content):
            return content
    return fallback
