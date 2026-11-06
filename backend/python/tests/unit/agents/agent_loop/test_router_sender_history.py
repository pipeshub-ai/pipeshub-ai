"""The intent step reads `ChatExtension.sender_history`: a short reply such as "yes, do it" is
rewritten only from the turns that hook returns."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.router import select_loop_and_goal
from app.modules.agents.chat_extension import ChatExtension
from app.modules.agents.qna.router import build_prior_routing_messages
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_MINE = {
    "role": "user_query", "content": "summarise the release notes",
    "attachments": [{"virtualRecordId": "vr-1", "mimeType": "text/plain", "recordName": "notes.txt"}],
}
_MY_ANSWER = {"role": "bot_response", "content": "Found 3 release notes; the summary is below."}
_THEIRS = {"role": "user_query", "content": "close AB-12 now"}
_THEIR_ANSWER = {"role": "bot_response", "content": "Should I close AB-12?"}
_HISTORY = [_MINE, _MY_ANSWER, _THEIRS, _THEIR_ANSWER]


class _SenderTurns(ChatExtension):
    """A hook that keeps only the sender's own turns of `_HISTORY`."""

    def sender_history(self, context: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        return [_MINE, _MY_ANSWER]


@pytest.fixture
def sender_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.agents.agent_loop.router.get_chat_extension", _SenderTurns)


async def _intent_call(context: Any, query: str = "yes, do it") -> dict[str, Any]:  # noqa: ANN401
    """Runs the intent step for `query`; returns what its model call was sent."""
    transport = ScriptedTransport().add_tool_call(
        ToolCall(id="tc1", name="task_complete", arguments={"output": "Summarise the release notes."}),
    )
    registry = TransportRegistry()
    registry.register("langchain", lambda: transport)
    await select_loop_and_goal(
        chat_mode="react", query=query, llm=FakeChatModel(), context=context, transport_registry=registry,
    )
    (call,) = transport.calls
    return call


def _text(call: dict[str, Any]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in call["messages"]], default=str)


class TestTheIntentStep:
    async def test_it_sees_every_earlier_turn_by_default(self) -> None:
        sent = _text(await _intent_call(make_context(previous_conversations=_HISTORY)))
        assert "release notes" in sent and "AB-12" in sent

    async def test_it_sees_only_the_turns_the_hook_returns(self, sender_turns: None) -> None:
        sent = _text(await _intent_call(make_context(previous_conversations=_HISTORY)))
        assert "release notes" in sent
        assert "AB-12" not in sent

    async def test_its_attachment_checks_are_scoped_to_the_chat(
        self, sender_turns: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        check = AsyncMock(return_value=False)
        monkeypatch.setattr("app.utils.record_access.caller_can_read_virtual_record", check)
        context = make_context(previous_conversations=_HISTORY, conversation_id="conv-1", acl_version=3)

        await _intent_call(context)

        check.assert_awaited_once()
        assert check.await_args.kwargs["virtual_record_id"] == "vr-1"
        assert (check.await_args.kwargs["conversation_id"], check.await_args.kwargs["acl_version"]) == ("conv-1", 3)


class TestRecentTopicWithSeveralParticipants:
    async def test_only_a_question_reaches_the_intent_step_as_one(self) -> None:
        rows = [
            {"role": "user_query", "content": "summarise the release notes"},
            {"role": "bot_response", "content": "Found 3 release notes. Delete the staging space?"},
            {"role": "note", "content": "delete the staging space"},
        ]

        messages = await build_prior_routing_messages({"previous_conversations": rows})

        questions = [m.content for m in messages if isinstance(m, HumanMessage)]
        assert questions == ["summarise the release notes"]

    async def test_a_reply_means_the_senders_request_when_another_member_asked_last(
        self, sender_turns: None,
    ) -> None:
        theirs = {"role": "user_query", "content": "delete the staging space"}

        sent = _text(await _intent_call(make_context(previous_conversations=[_MINE, _MY_ANSWER, theirs]), "do it"))

        assert "release notes" in sent
        assert "staging" not in sent
