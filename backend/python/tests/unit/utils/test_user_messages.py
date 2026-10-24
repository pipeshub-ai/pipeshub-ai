
"""The API failure messages people read in a toast."""

from __future__ import annotations

import pytest

from app.utils.user_messages import (
    PEOPLE_GONE,
    SOMETHING_WENT_WRONG,
    action_failed,
    folder_in_trash,
    not_found,
    provider_failure,
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
    assert message.startswith(
        "This connector was removed, or you no longer have access."
    )
    assert "Refresh the page" in message


@pytest.mark.parametrize("message", MESSAGES)
def test_every_message_is_a_plain_sentence_with_a_next_step(
    message: str,
) -> None:
    assert message[0].isupper(), message
    assert message.endswith("."), message

    lowered = message.lower()
    for word in JARGON:
        assert word not in lowered, f"{word!r} in {message!r}"

    next_steps = (
        "try again",
        "refresh the page",
        "remove them",
        "contact your admin",
    )
    assert any(step in lowered for step in next_steps), message


@pytest.mark.parametrize("message", MESSAGES)
def test_no_message_carries_an_id_or_placeholder(message: str) -> None:
    assert "{" not in message and "}" not in message, message


# Tests for folder_in_trash


def test_folder_in_trash_includes_folder_name_and_restore_hint() -> None:
    message = folder_in_trash("Design", "move items into it")

    assert message == (
        "'Design' is in Recently deleted, so you can't move items into it. "
        "Restore it first, or choose another folder."
    )


@pytest.mark.parametrize("folder_name", [None, ""])
def test_folder_in_trash_handles_missing_folder_name(
    folder_name: str | None,
) -> None:
    message = folder_in_trash(folder_name, "upload files to it")

    assert message.startswith("That folder is in Recently deleted")
    assert "Restore it first" in message


# Tests for provider_failure


def test_provider_failure_passes_through_client_error_reason() -> None:
    result = {"code": 409, "reason": "Already there"}

    assert provider_failure(result, "delete this file") == (
        409,
        "Already there",
    )


def test_provider_failure_accepts_string_status_code() -> None:
    result = {"code": "404", "reason": "Gone"}

    assert provider_failure(result, "delete this file") == (
        404,
        "Gone",
    )


@pytest.mark.parametrize(
    "result",
    [
        {"code": 500, "reason": "db down"},
        {"code": 403},
        {"reason": "nope"},
        None,
    ],
)
def test_provider_failure_hides_unsafe_or_missing_reasons(
    result: object,
) -> None:
    assert provider_failure(result, "delete this file") == (
        500,
        action_failed("delete this file"),
    )
