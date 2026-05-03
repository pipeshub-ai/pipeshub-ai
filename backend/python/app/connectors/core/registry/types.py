"""
Shared types for connector and toolset registries.

This module contains common data structures used across the registry system
to avoid circular dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class FieldType(str, Enum):
    """Standard field types for authentication and configuration fields"""
    TEXT = "TEXT"
    PASSWORD = "PASSWORD"
    CHECKBOX = "CHECKBOX"
    SELECT = "SELECT"
    MULTISELECT = "MULTISELECT"
    TEXTAREA = "TEXTAREA"
    NUMBER = "NUMBER"
    URL = "URL"
    EMAIL = "EMAIL"


class ValidationRuleType(str, Enum):
    """Rule types executed against file content after upload.

    String values are the wire format consumed by the frontend's
    ``executeValidationRules`` function — do not rename without updating
    the TypeScript ``ValidationRuleType`` const in types.ts.
    """
    JSON_VALID = "json_valid"
    JSON_HAS_FIELDS = "json_has_fields"
    JSON_FIELD_EQUALS = "json_field_equals"
    TEXT_CONTAINS = "text_contains"
    TEXT_NOT_CONTAINS = "text_not_contains"


class FileContentValidationRule(BaseModel):
    """One validation step for uploaded connector file contents (wire shape shared with the frontend).

    Use :meth:`model_json_schema` for OpenAPI/JSON Schema tooling; TS types in
    ``frontend`` remain the contract for the dashboard until codegen is wired.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True, populate_by_name=True)

    type: ValidationRuleType
    required_fields: Optional[list[str]] = Field(
        default=None,
        validation_alias=AliasChoices("requiredFields", "required_fields", "fields"),
        serialization_alias="requiredFields",
    )
    field: Optional[str] = None
    value: Optional[str] = None
    pattern: Optional[str] = None
    error_message: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("errorMessage", "error_message"),
        serialization_alias="errorMessage",
    )

    @model_validator(mode="after")
    def _require_payload_for_rule_type(self) -> Self:
        t = self.type

        if t == ValidationRuleType.JSON_HAS_FIELDS.value:
            if not self.required_fields:
                raise ValueError(
                    "json_has_fields rules require a non-empty 'required_fields' / 'requiredFields' list"
                )
        elif t == ValidationRuleType.JSON_FIELD_EQUALS.value:
            if self.field is None or self.field == "":
                raise ValueError("json_field_equals rules require 'field'")
            if self.value is None:
                raise ValueError("json_field_equals rules require 'value'")
        elif t in (ValidationRuleType.TEXT_CONTAINS.value, ValidationRuleType.TEXT_NOT_CONTAINS.value):
            if not self.pattern:
                raise ValueError(f"{t} rules require a non-empty 'pattern'")
        return self


@dataclass
class AuthField:
    """Represents an authentication field"""
    name: str
    display_name: str
    field_type: str = "TEXT"
    placeholder: str = ""
    description: str = ""
    required: bool = True
    default_value: Any = ""
    min_length: int = 1
    max_length: int = 1000
    is_secret: bool = False
    usage: Literal["CONFIGURE", "AUTHENTICATE", "BOTH"] = "BOTH"
    accepted_file_types: list[str] = field(default_factory=list)
    validation_rules: list[FileContentValidationRule] = field(default_factory=list)


@dataclass
class CustomField:
    """Represents a custom field for sync configuration"""
    name: str
    display_name: str
    field_type: str
    description: str = ""
    required: bool = False
    default_value: Any = ""
    options: list[str] = field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    is_secret: bool = False
    non_editable: bool = False


@dataclass
class DocumentationLink:
    """Represents a documentation link"""
    title: str
    url: str
    doc_type: str
