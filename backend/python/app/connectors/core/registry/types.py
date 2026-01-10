"""
Shared types for connector and toolset registries.

This module contains common data structures used across the registry system
to avoid circular dependencies.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional


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


@dataclass
class CustomField:
    """Represents a custom field for sync configuration"""
    name: str
    display_name: str
    field_type: str
    description: str = ""
    required: bool = False
    default_value: Any = ""
    options: List[str] = field(default_factory=list)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    is_secret: bool = False


@dataclass
class DocumentationLink:
    """Represents a documentation link"""
    title: str
    url: str
    doc_type: str


