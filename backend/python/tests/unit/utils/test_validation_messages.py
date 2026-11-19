"""Plain-language wording for request validation errors."""

from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.utils.validation_messages import (
    GENERIC_SUMMARY,
    field_label,
    friendly_validation_errors,
    validation_error_text,
    without_input,
)


class _Search(BaseModel):
    query: str = Field(min_length=1)
    pageSize: int = Field(ge=1, le=100)
    mode: Literal["fast", "deep"]
    tags: list[str] = Field(min_length=2)
    user_email: str

    @field_validator("user_email")
    @classmethod
    def _has_at(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("Enter an email address like name@company.com.")
        return value


def _messages(payload: dict) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        _Search(**payload)
    # FastAPI prefixes each location with the request part, as here.
    errors = [{**e, "loc": ("body", *e["loc"])} for e in caught.value.errors()]
    return [e["msg"] for e in friendly_validation_errors(errors)[1]]


def test_stock_pydantic_messages_become_plain_words_naming_the_field() -> None:
    assert _messages({"query": "", "pageSize": "x", "mode": "slow", "tags": ["a"], "user_email": "x@y"}) == [
        "Query can't be empty.",
        "Page size must be a whole number.",
        "Mode must be one of: 'fast' or 'deep'.",
        "Add at least 2 items to tags.",
    ]


def test_missing_fields_and_limits() -> None:
    assert _messages({"pageSize": 500, "mode": "fast", "tags": ["a", "b"], "user_email": "x@y"}) == [
        "Query is required.",
        "Page size must be at most 100.",
    ]


def test_a_validator_s_own_message_is_kept_without_pydantic_s_prefix() -> None:
    assert _messages({"query": "q", "pageSize": 1, "mode": "fast", "tags": ["a", "b"], "user_email": "nope"}) == [
        "Enter an email address like name@company.com."
    ]


def test_labels_skip_the_request_part_and_list_indexes() -> None:
    assert field_label(["body", "filters", 0, "departmentName"]) == "Department name"
    assert field_label(["body"]) == "The request"
    assert field_label([]) == "The request"


def test_summary_joins_distinct_messages_and_has_a_fallback() -> None:
    summary, detail = friendly_validation_errors(
        [
            {"type": "missing", "loc": ["body", "query"], "msg": "Field required"},
            {"type": "missing", "loc": ["query", "query"], "msg": "Field required"},
        ]
    )
    assert summary == "Query is required."
    assert len(detail) == 2
    assert friendly_validation_errors([]) == (GENERIC_SUMMARY, [])


# --- The rejected input never comes back (F47) ---------------------------------------------------------------------

SEED = "F47SEED-ada@example.com"


class _Attachment(BaseModel):
    fileName: str
    size: int


class _Upload(BaseModel):
    attachments: list[_Attachment]
    note: str = Field(default="", max_length=10)
    budget: Literal["short", "full"] | None = None
    runId: str | None = None
    size: int | None = None

    @field_validator("size")
    @classmethod
    def _positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise AssertionError("Size must be positive.")  # pytest rewrites a bare assert's message
        return value

    @field_validator("runId")
    @classmethod
    def _uuid_like(cls, value: str | None) -> str | None:
        if value is not None and "-" not in value:
            raise ValueError("Invalid runId: must be a UUID.")
        return value


def _error(payload: object) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _Upload.model_validate(payload)
    return caught.value


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Longer than 50 characters: str() would show a truncated repr, head and tail.
        ({"attachments": [], "note": SEED * 3}, "note: String should have at most 10 characters"),
        ({"attachments": [], "budget": SEED}, "budget: Input should be 'short' or 'full'"),
        ({"attachments": SEED}, "attachments: Input should be a valid list"),
        # The input of a missing field is the whole enclosing item: here a file name.
        ({"attachments": [{"fileName": SEED}]}, "attachments.0.size: Field required"),
        ({"attachments": [{"fileName": "f", "size": SEED}]},
         "attachments.0.size: Input should be a valid integer, unable to parse string as an integer"),
    ],
)
def test_the_text_names_each_field_and_rule_but_not_the_value(payload: dict, expected: str) -> None:
    error = _error(payload)
    assert SEED[:20] in str(error)  # control: pydantic's own text echoes it
    text = validation_error_text(error)
    assert text == expected
    assert SEED[:20] not in text
    assert "input_value" not in text and "errors.pydantic.dev" not in text


def test_errors_are_joined_in_order() -> None:
    assert validation_error_text(_error({"attachments": [{}], "note": SEED})) == (
        "attachments.0.fileName: Field required; attachments.0.size: Field required; "
        "note: String should have at most 10 characters"
    )


def test_an_error_with_no_location_is_its_message_alone() -> None:
    assert validation_error_text(_error([SEED])) == "Input should be a valid dictionary or instance of _Upload"


def test_a_validator_s_own_message_loses_pydantic_s_prefix() -> None:
    assert validation_error_text(_error({"attachments": [], "runId": "x"})) == "runId: Invalid runId: must be a UUID."
    assert validation_error_text(_error({"attachments": [], "size": -1})) == "size: Size must be positive."


def test_friendly_errors_drop_the_input_but_keep_the_rest() -> None:
    error = _error({"attachments": [], "note": SEED})
    errors = [{**e, "loc": ("body", *e["loc"])} for e in error.errors()]
    (entry,) = friendly_validation_errors(errors)[1]
    assert "input" not in entry
    assert entry["loc"] == ("body", "note")
    assert entry["type"] == "string_too_long"
    assert entry["ctx"] == {"max_length": 10}
    assert entry["msg"] == "Note must be at most 10 characters."  # ctx still drives the wording
    assert SEED not in str(entry)


def test_without_input_removes_only_the_input_and_leaves_the_argument_alone() -> None:
    errors = [{"type": "t", "loc": ["q"], "msg": "m", "input": SEED, "ctx": {"ge": 1}, "url": "u"}]
    assert without_input(errors) == [{"type": "t", "loc": ["q"], "msg": "m", "ctx": {"ge": 1}, "url": "u"}]
    assert errors[0]["input"] == SEED
