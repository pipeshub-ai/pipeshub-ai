"""PH10-12: `mentions` on both chat request models is typed, ref-only, and defaults to []."""
import pytest
from pydantic import ValidationError

from app.api.routes.agent import ChatQuery as AgentChatQuery
from app.api.routes.chatbot import ChatQuery


@pytest.mark.parametrize("model", [ChatQuery, AgentChatQuery])
def test_absent_mentions_default_to_empty(model) -> None:
    assert model(query="q").mentions == []


@pytest.mark.parametrize("model", [ChatQuery, AgentChatQuery])
def test_mentions_are_accepted_as_refs(model) -> None:
    q = model(query="q", mentions=[{"type": "participant", "ref": "participant_2"}, {"type": "agent", "ref": "agent:self"}])
    assert [(m.type, m.ref) for m in q.mentions] == [("participant", "participant_2"), ("agent", "agent:self")]


@pytest.mark.parametrize("model", [ChatQuery, AgentChatQuery])
@pytest.mark.parametrize(
    "bad",
    [
        {"type": "admin", "ref": "participant_1"},
        {"type": "user", "id": "6abf35278cf29be1a243f0aa"},
        {"type": "participant", "ref": "6abf35278cf29be1a243f0aa"},
    ],
)
def test_other_shapes_are_rejected(model, bad) -> None:
    with pytest.raises(ValidationError):
        model(query="q", mentions=[bad])
