"""Checks shared by the tests that read a chat stream."""

from collections.abc import Iterable, Sequence
from typing import Any

BOT_RESPONSE = "bot_response"


def read_finished_run(stream: Iterable[Any]) -> list[Any]:
    """Read a chat stream to its end and check that the run produced an answer."""
    events = list(stream)
    errors = [event.data for event in events if event.event == "RUN_ERROR"]
    assert not errors, f"stream reported an error: {errors}"
    names = [event.event for event in events]
    assert "TEXT_MESSAGE_CONTENT" in names, f"no answer text in the stream: {names}"
    assert "RUN_FINISHED" in names, f"stream ended without RUN_FINISHED: {names}"
    return events


def created_conversation_id(events: Sequence[Any]) -> str:
    for event in events:
        if event.event == "CUSTOM" and event.data.get("name") == "conversation_created":
            return event.data["value"]["conversationId"]
    raise AssertionError("the stream did not announce a new conversation")


def last_answer_id(messages: Sequence[Any]) -> str:
    answers = [message for message in messages if message.message_type == BOT_RESPONSE]
    assert answers, "the conversation has no answer message"
    return answers[-1].id
