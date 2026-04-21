"""
Shared types for connector and toolset registries.

This module contains common data structures used across the registry system
to avoid circular dependencies.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


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
    JSON_VALID        = "json_valid"
    JSON_HAS_FIELDS   = "json_has_fields"
    JSON_FIELD_EQUALS = "json_field_equals"
    TEXT_CONTAINS     = "text_contains"
    TEXT_NOT_CONTAINS = "text_not_contains"


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
    validation_rules: list[dict] = field(default_factory=list)


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

