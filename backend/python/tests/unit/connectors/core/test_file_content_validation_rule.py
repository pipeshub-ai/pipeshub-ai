"""Tests for ``FileContentValidationRule`` (connector file upload validation schema)."""

import pytest
from pydantic import ValidationError

from app.connectors.core.registry.types import FileContentValidationRule, ValidationRuleType


def test_json_has_fields_requires_fields() -> None:
    with pytest.raises(ValidationError):
        FileContentValidationRule(type=ValidationRuleType.JSON_HAS_FIELDS, fields=[])


def test_json_field_equals_requires_field_and_value() -> None:
    with pytest.raises(ValidationError):
        FileContentValidationRule(
            type=ValidationRuleType.JSON_FIELD_EQUALS,
            field="type",
            error_message="x",
        )
    with pytest.raises(ValidationError):
        FileContentValidationRule(
            type=ValidationRuleType.JSON_FIELD_EQUALS,
            value="service_account",
            error_message="x",
        )


def test_text_rule_requires_pattern() -> None:
    with pytest.raises(ValidationError):
        FileContentValidationRule(
            type=ValidationRuleType.TEXT_CONTAINS,
            error_message="x",
        )


def test_accepts_wire_alias_error_message() -> None:
    r = FileContentValidationRule.model_validate(
        {"type": "json_valid", "errorMessage": "bad json"}
    )
    assert r.error_message == "bad json"
