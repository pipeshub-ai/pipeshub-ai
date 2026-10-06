import pytest

from app.agents.agent_loop import factory
from app.modules.agents.collaboration.models import (
    CollaborationContext,
    Participant,
    ResumeRequest,
)
from app.modules.agents.collaboration.resume import (
    last_real_user_query,
    resolve_resume,
)

PREFIX = 'User selections:\n1. "Which?" → A'


def collab(sender: str = "participant_1") -> CollaborationContext:
    return CollaborationContext(
        participants=[
            Participant(ref="participant_1", displayName="A", isCurrentSender=sender == "participant_1"),
            Participant(ref="participant_2", displayName="B", isCurrentSender=sender == "participant_2"),
        ],
        currentSenderRef=sender,
    )


def turn(role: str, content: str, ref: str | None = None) -> dict:
    d = {"role": role, "content": content}
    if ref:
        d["authorRef"] = ref
    return d


RESUME = ResumeRequest(toolCallMessageId="m1")


def test_factory_reexports() -> None:
    assert factory.last_real_user_query is last_real_user_query


def test_solo_prefix_resumes_with_last_real_query() -> None:
    prev = [turn("user_query", "goal"), turn("bot_response", "Which?")]
    d = resolve_resume(PREFIX, prev, None, None)
    assert d.answers == PREFIX and d.goal == "goal"


def test_solo_ignores_resume_field() -> None:
    prev = [turn("user_query", "goal")]
    assert resolve_resume(PREFIX, prev, None, RESUME).answers == PREFIX


@pytest.mark.parametrize("ws", [" ", "\n", "\t\n  "])
def test_solo_leading_whitespace(ws: str) -> None:
    assert resolve_resume(ws + PREFIX, [turn("user_query", "g")], None, None).answers is not None


def test_collab_prefix_without_resume_is_plain_text() -> None:
    prev = [turn("user_query", "B goal", "participant_2"), turn("bot_response", "Which?")]
    d = resolve_resume(PREFIX, prev, collab("participant_1"), None)
    assert d.answers is None and d.goal == PREFIX


def test_collab_newest_user_turn_by_other_is_not_resume() -> None:
    prev = [turn("user_query", "A goal", "participant_1"), turn("bot_response", "x"),
            turn("user_query", "B goal", "participant_2"), turn("bot_response", "Which?")]
    d = resolve_resume(PREFIX, prev, collab("participant_1"), RESUME)
    assert d.answers is None and d.goal == PREFIX


def test_collab_resume_binds_own_goal_past_interleaved_turn() -> None:
    prev = [turn("user_query", "B goal", "participant_2"), turn("bot_response", "Which?"),
            turn("user_query", "A wrote in between", "participant_1"),
            turn("user_query", "B again", "participant_2")]
    # B resumes; newest user_query is B's -> own goal is B's latest real query
    d = resolve_resume(PREFIX, prev, collab("participant_2"), RESUME)
    assert d.answers == PREFIX and d.goal == "B again"


def test_collab_goal_skips_other_authors() -> None:
    prev = [turn("user_query", "B goal", "participant_2"), turn("bot_response", "r"),
            turn("user_query", "A mid", "participant_1"), turn("bot_response", "r"),
            turn("user_query", "B resume-row " , "participant_2")]
    prev[-1]["content"] = 'User selections: x'
    d = resolve_resume(" \n" + PREFIX, prev, collab("participant_2"), RESUME)
    assert d.goal == "B goal"


def test_collab_resume_without_prefix_is_normal() -> None:
    prev = [turn("user_query", "g", "participant_1")]
    d = resolve_resume("hello", prev, collab(), RESUME)
    assert d.answers is None and d.goal == "hello"


def test_collab_empty_history_not_resume() -> None:
    assert resolve_resume(PREFIX, [], collab(), RESUME).answers is None
    assert resolve_resume(PREFIX, None, collab(), RESUME).answers is None


def test_last_real_user_query_author_filter_fallback() -> None:
    prev = [turn("user_query", "B goal", "participant_2")]
    assert last_real_user_query(prev, "fb", author_ref="participant_1") == "fb"
    assert last_real_user_query(prev, "fb") == "B goal"


def test_collab_a_note_after_the_card_neither_answers_it_nor_steals_its_binding() -> None:
    # B was asked; A then posts a note that looks like an answer. Notes are information only (D13).
    prev = [turn("user_query", "B goal", "participant_2"), turn("bot_response", "Which?"),
            turn("note", PREFIX, "participant_1")]
    as_b = resolve_resume(PREFIX, prev, collab("participant_2"), RESUME)
    assert as_b.answers == PREFIX and as_b.goal == "B goal"
    as_a = resolve_resume(PREFIX, prev, collab("participant_1"), RESUME)
    assert as_a.answers is None
    assert last_real_user_query([turn("user_query", "goal"), turn("note", "a note, not a goal")], "fb") == "goal"
