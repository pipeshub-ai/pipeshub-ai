import logging
from typing import Any, Final

from pydantic import BaseModel

from app.modules.agents.collaboration.models import CollaborationContext, ResumeRequest

logger = logging.getLogger(__name__)

ASK_USER_QUESTION_RESUME_PREFIX: Final = "User selections:"


class ResumeDecision(BaseModel):
    answers: str | None
    goal: str


def is_ask_user_question_resume_query(text: str | None) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(ASK_USER_QUESTION_RESUME_PREFIX)


def last_real_user_query(
    previous_conversations: list[dict[str, Any]] | None,
    fallback: str,
    *,
    author_ref: str | None = None,
) -> str:
    """Original user goal when this request is an ask_user_question resume."""
    for turn in reversed(previous_conversations or []):
        if turn.get("role") != "user_query":
            continue
        if author_ref is not None and turn.get("authorRef") != author_ref:
            continue
        content = str(turn.get("content") or "").strip()
        if content and not is_ask_user_question_resume_query(content):
            return content
    return fallback


def resolve_resume(
    query: str,
    prev: list[dict[str, Any]] | None,
    c: CollaborationContext | None,
    resume: ResumeRequest | None,
) -> ResumeDecision:
    has_prefix = is_ask_user_question_resume_query(query)
    if c is None:
        if has_prefix:
            return ResumeDecision(answers=query, goal=last_real_user_query(prev, query))
        return ResumeDecision(answers=None, goal=query)

    if not has_prefix or resume is None:
        return ResumeDecision(answers=None, goal=query)

    newest = next((t for t in reversed(prev or []) if t.get("role") == "user_query"), None)
    if newest is None or newest.get("authorRef") != c.currentSenderRef:
        logger.warning("Ignoring resume: newest user turn is not the current sender's")
        return ResumeDecision(answers=None, goal=query)

    return ResumeDecision(
        answers=query,
        goal=last_real_user_query(prev, query, author_ref=c.currentSenderRef),
    )
