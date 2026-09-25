"""A missing credential skips while developing and fails on the nightly."""

from __future__ import annotations

import pytest

from helper.source_credentials import (
    REQUIRE_ENV,
    missing_env,
    source_unavailable,
    secrets_required,
)

pytestmark = pytest.mark.unit


def test_it_skips_when_nothing_required(monkeypatch) -> None:
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    with pytest.raises(BaseException) as caught:
        source_unavailable("Jira credentials are not set.", secrets=["JIRA_TEST_EMAIL"])
    assert caught.typename == "Skipped"


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_it_fails_on_a_run_that_was_meant_to_cover_it(monkeypatch, value: str) -> None:
    monkeypatch.setenv(REQUIRE_ENV, value)
    with pytest.raises(BaseException) as caught:
        source_unavailable("Jira credentials are not set.", secrets=["JIRA_TEST_EMAIL"])
    assert caught.typename == "Failed"
    message = str(caught.value)
    # whoever reads a red nightly needs the variable name and what to do
    assert "Jira credentials are not set." in message
    assert "JIRA_TEST_EMAIL" in message
    assert "shard" in message


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_other_values_leave_it_a_skip(monkeypatch, value: str) -> None:
    monkeypatch.setenv(REQUIRE_ENV, value)
    assert secrets_required() is False


def test_it_names_the_credentials_generically_when_none_are_given(monkeypatch) -> None:
    monkeypatch.setenv(REQUIRE_ENV, "1")
    with pytest.raises(BaseException) as caught:
        source_unavailable("Notion is not reachable.")
    assert "credentials for this connector" in str(caught.value)


def test_missing_env_lists_only_the_unset_ones(monkeypatch) -> None:
    monkeypatch.setenv("PRESENT_ONE", "x")
    monkeypatch.delenv("ABSENT_ONE", raising=False)
    monkeypatch.setenv("BLANK_ONE", "")
    assert missing_env(["PRESENT_ONE", "ABSENT_ONE", "BLANK_ONE"]) == [
        "ABSENT_ONE",
        "BLANK_ONE",
    ]


def test_a_caller_can_say_this_run_was_meant_to_cover_it(monkeypatch) -> None:
    """Not every caller is a connector suite, where the shard settles it."""
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    with pytest.raises(BaseException) as caught:
        source_unavailable(
            "openAI: no live credentials", secrets=["TEST_OPENAI_API_KEY"], required=True
        )
    assert caught.typename == "Failed"


def test_a_caller_can_say_this_run_was_not(monkeypatch) -> None:
    monkeypatch.setenv(REQUIRE_ENV, "1")
    with pytest.raises(BaseException) as caught:
        source_unavailable(
            "groq: no live credentials", secrets=["TEST_GROQ_API_KEY"], required=False
        )
    assert caught.typename == "Skipped"
