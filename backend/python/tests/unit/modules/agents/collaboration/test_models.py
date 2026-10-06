import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.agents.collaboration import (
    CollaborationContext,
    PreviousConversationTurn,
    ResumeRequest,
    turns_to_dicts,
)

FIXTURES = Path(__file__).parents[3] / "agents" / "adapter" / "fixtures"


def _roster(n: int = 2, current: int = 2) -> list[dict]:
    return [
        {"ref": f"participant_{i}", "displayName": f"User {i}", "isCurrentSender": i == current}
        for i in range(1, n + 1)
    ]


def _ctx(**over: object) -> dict:
    body = {"participants": _roster(), "currentSenderRef": "participant_2"}
    body.update(over)
    return body


def test_valid_context() -> None:
    c = CollaborationContext(**_ctx())
    assert c.currentSenderRef == "participant_2"
    assert c.model_dump()["participants"][1]["isCurrentSender"] is True


@pytest.mark.parametrize("ref", ["participant_0", "alice", "participant_1000", "participant_01", "Participant_1"])
def test_bad_ref_pattern_rejected(ref) -> None:
    roster = _roster()
    roster[0]["ref"] = ref
    with pytest.raises(ValidationError):
        CollaborationContext(**_ctx(participants=roster))


def test_bad_current_sender_ref_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        CollaborationContext(**_ctx(currentSenderRef="bob"))


def test_duplicate_refs_rejected() -> None:
    roster = _roster()
    roster[0]["ref"] = "participant_2"
    roster[0]["isCurrentSender"] = False
    with pytest.raises(ValidationError, match="unique"):
        CollaborationContext(**_ctx(participants=roster))


def test_two_current_senders_rejected() -> None:
    roster = _roster()
    roster[0]["isCurrentSender"] = True
    with pytest.raises(ValidationError, match="exactly one"):
        CollaborationContext(**_ctx(participants=roster))


def test_no_current_sender_rejected() -> None:
    roster = _roster(current=0)
    with pytest.raises(ValidationError, match="exactly one"):
        CollaborationContext(**_ctx(participants=roster))


def test_current_sender_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="currentSenderRef"):
        CollaborationContext(**_ctx(currentSenderRef="participant_1"))


def test_participant_count_bounds() -> None:
    with pytest.raises(ValidationError):
        CollaborationContext(participants=_roster(1, 1), currentSenderRef="participant_1")
    with pytest.raises(ValidationError):
        CollaborationContext(participants=_roster(51, 2), currentSenderRef="participant_2")
    CollaborationContext(participants=_roster(50, 2), currentSenderRef="participant_2")


def test_display_name_cap() -> None:
    roster = _roster()
    roster[0]["displayName"] = "x" * 201
    with pytest.raises(ValidationError):
        CollaborationContext(**_ctx(participants=roster))


def test_identity_keys_forbidden() -> None:
    with pytest.raises(ValidationError):
        CollaborationContext(**_ctx(userId="u1"))
    roster = _roster()
    roster[0]["orgId"] = "o1"
    with pytest.raises(ValidationError):
        CollaborationContext(**_ctx(participants=roster))


def test_resume_request_bounds_and_forbid() -> None:
    assert ResumeRequest(toolCallMessageId="abc").toolCallMessageId == "abc"
    for bad in ("", "x" * 65):
        with pytest.raises(ValidationError):
            ResumeRequest(toolCallMessageId=bad)
    with pytest.raises(ValidationError):
        ResumeRequest(toolCallMessageId="a", extra=1)


def test_turn_author_ref_pattern() -> None:
    with pytest.raises(ValidationError):
        PreviousConversationTurn(role="user_query", content="x", authorRef="alice")
    assert PreviousConversationTurn(role="user_query", authorRef="participant_3").authorRef == "participant_3"


def test_turns_round_trip_node_fixture() -> None:
    turns = json.loads((FIXTURES / "solo_history_input.json").read_text())["previousConversations"]
    validated = [PreviousConversationTurn(**t) for t in turns]
    assert turns_to_dicts(validated) == turns


def test_content_never_coerced() -> None:
    for content in (None, 42, ["a"], {"k": 1}):
        turn = PreviousConversationTurn(role="user_query", content=content)
        assert turn.content == content and type(turn.content) is type(content)


def test_unset_keys_stay_absent() -> None:
    assert turns_to_dicts([PreviousConversationTurn(role="bot_response")]) == [{"role": "bot_response"}]
    assert turns_to_dicts([PreviousConversationTurn(role="user_query", authorRef="participant_1")]) == [
        {"role": "user_query", "authorRef": "participant_1"}
    ]


@pytest.mark.parametrize("path", ["app.api.routes.chatbot", "app.api.routes.agent"])
class TestChatQueryTyping:
    def _model(self, path: str) -> type:
        import importlib

        return importlib.import_module(path).ChatQuery

    def test_collaboration_with_identity_keys_is_422(self, path) -> None:
        body = _ctx(userId="u1")
        with pytest.raises(ValidationError):
            self._model(path)(query="q", collaboration=body)

    def test_collaboration_accepted_and_typed(self, path) -> None:
        q = self._model(path)(query="q", collaboration=_ctx(), aclVersion=3)
        assert isinstance(q.collaboration, CollaborationContext)
        assert q.aclVersion == 3

    def test_previous_conversations_typed_and_dumped_unchanged(self, path) -> None:
        turns = [{"role": "user_query", "content": "hi", "authorRef": "participant_1", "attachments": []}]
        q = self._model(path)(query="q", previousConversations=turns)
        assert isinstance(q.previousConversations[0], PreviousConversationTurn)
        assert turns_to_dicts(q.previousConversations) == turns

    def test_defaults_are_solo(self, path) -> None:
        q = self._model(path)(query="q")
        assert q.collaboration is None and q.resume is None and q.previousConversations == []
