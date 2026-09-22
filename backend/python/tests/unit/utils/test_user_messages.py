"""The API failure messages people read in a toast."""

from __future__ import annotations

import pytest

from app.utils.user_messages import (
    PEOPLE_GONE,
    SOMETHING_WENT_WRONG,
    action_failed,
    not_found,
)

MESSAGES = [
    action_failed("delete this folder"),
    not_found("This connector"),
    PEOPLE_GONE,
    SOMETHING_WENT_WRONG,
]

# Words that mean something to us and nothing to the person reading the toast.
JARGON = [
    "graph",
    "vector store",
    "connector service",
    "AQL",
    "traceback",
    "exception",
    "null",
    "none",
    "500",
    "str(e)",
]


def test_an_action_failure_says_what_failed_and_what_to_do() -> None:
    message = action_failed("delete this folder")
    assert message == (
        "We couldn't delete this folder. Please try again; if it keeps failing, contact your admin."
    )


def test_a_missing_thing_does_not_say_whether_it_exists() -> None:
    message = not_found("This connector")
    assert message.startswith("This connector was removed, or you no longer have access.")
    assert "Refresh the page" in message


@pytest.mark.parametrize("message", MESSAGES)
def test_every_message_is_a_plain_sentence_with_a_next_step(message: str) -> None:
    assert message[0].isupper(), message
    assert message.endswith("."), message
    lowered = message.lower()
    for word in JARGON:
        assert word not in lowered, f"{word!r} in {message!r}"
    next_steps = ("try again", "refresh the page", "remove them", "contact your admin")
    assert any(step in lowered for step in next_steps), message


@pytest.mark.parametrize("message", MESSAGES)
def test_no_message_carries_an_id_or_placeholder(message: str) -> None:
    assert "{" not in message and "}" not in message, message
