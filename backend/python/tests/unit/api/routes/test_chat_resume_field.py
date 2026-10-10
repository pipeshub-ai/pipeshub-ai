"""Node sends `resume: {toolCallMessageId}` on a follow-up that answers an ask_user_question card.

Both chat request models accept it and expose it as a typed `ResumeRequest`.
"""
import pytest

from app.api.routes.agent import ChatQuery as AgentChatQuery
from app.api.routes.chatbot import ChatQuery


@pytest.mark.parametrize("model", [ChatQuery, AgentChatQuery])
def test_resume_is_accepted_and_typed(model) -> None:
    query = model(
        query="User selections: EU",
        resume={"toolCallMessageId": "6abf35278cf29be1a243f001"},
        runId="3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c",
    )

    assert query.query == "User selections: EU"
    assert query.runId == "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c"
    assert query.resume.toolCallMessageId == "6abf35278cf29be1a243f001"


@pytest.mark.parametrize("model", [ChatQuery, AgentChatQuery])
def test_resume_absent_defaults_none(model) -> None:
    assert model(query="q").resume is None
