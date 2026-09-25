"""Plain-language wording for request validation errors."""

from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.utils.validation_messages import (
    GENERIC_SUMMARY,
    field_label,
    friendly_validation_errors,
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
