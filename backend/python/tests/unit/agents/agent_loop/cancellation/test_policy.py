"""`may_cancel` (`app/agents/agent_loop/cancellation/policy.py`)."""

from __future__ import annotations

import pytest

from app.agents.agent_loop.cancellation.policy import CancelRequester, may_cancel
from app.agents.agent_loop.cancellation.registry import RunOwner


def _owner(conversation_id: str | None = "conv-x", org_id: str = "org-1") -> RunOwner:
    return RunOwner(user_id="user-a", org_id=org_id, conversation_id=conversation_id)


def _participant(conversation_id: str | None = "conv-x", org_id: str = "org-1") -> CancelRequester:
    return CancelRequester(
        user_id="user-b", org_id=org_id, conversation_id=conversation_id, via_participant_grant=True
    )


class TestUserPath:
    def test_same_user_and_org_may_cancel(self) -> None:
        assert may_cancel(_owner(), CancelRequester(user_id="user-a", org_id="org-1")) is True

    def test_other_user_may_not(self) -> None:
        assert may_cancel(_owner(), CancelRequester(user_id="user-b", org_id="org-1")) is False

    def test_plain_run_owner_requester_stays_on_the_user_path(self) -> None:
        assert may_cancel(_owner(), RunOwner(user_id="user-b", org_id="org-1", conversation_id="conv-x")) is False

    def test_conversation_mismatch_is_refused(self) -> None:
        requester = CancelRequester(user_id="user-a", org_id="org-1", conversation_id="conv-y")
        assert may_cancel(_owner(), requester) is False


class TestParticipantPath:
    def test_same_org_and_conversation_without_a_user_match(self) -> None:
        assert may_cancel(_owner(), _participant()) is True

    def test_owner_without_conversation_is_refused(self) -> None:
        assert may_cancel(_owner(conversation_id=None), _participant()) is False

    def test_requester_without_conversation_is_refused(self) -> None:
        assert may_cancel(_owner(), _participant(conversation_id=None)) is False

    def test_other_conversation_is_refused(self) -> None:
        assert may_cancel(_owner(), _participant(conversation_id="conv-y")) is False

    def test_other_org_is_refused(self) -> None:
        assert may_cancel(_owner(), _participant(org_id="org-2")) is False

    @pytest.mark.parametrize("conversation_id", [None, ""])
    def test_empty_conversation_on_both_sides_is_refused(self, conversation_id: str | None) -> None:
        assert may_cancel(_owner(conversation_id), _participant(conversation_id)) is False


def test_legacy_payload_without_the_flag_fails_closed() -> None:
    legacy = CancelRequester.model_validate_json(
        RunOwner(user_id="user-b", org_id="org-1", conversation_id="conv-x").model_dump_json()
    )
    assert legacy.via_participant_grant is False
    assert may_cancel(_owner(), legacy) is False
