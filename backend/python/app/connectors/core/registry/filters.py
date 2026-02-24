"""
Filter Types, Operators, and Models for Connector Filtering System.

This module provides:
1. FilterType - Supported filter data types with fixed operators per type
2. FilterCategory - SYNC (API-level) vs INDEXING (record-level)
3. SyncFilterKey / IndexingFilterKey - Common filter keys for runtime access
4. FilterField - Schema definition for UI (used in add_filter_field decorator)
5. Filter/FilterCollection - Runtime filter parsing from config
"""

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from enum import Enum
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator

from app.config.configuration_service import ConfigurationService

# Module logger for filter parsing warnings
_logger = logging.getLogger(__name__)

# Type alias for filter values (string, bool, list, number, datetime tuple, or None)
# Datetime values are stored as (start, end) tuple of epoch integers in milliseconds
FilterValue = Union[str, bool, int, float, List[str], Tuple[Optional[int], Optional[int]], None]
MAX_DATETIME_TUPLE_LENGTH = 2  # (start, end)

class FilterType(str, Enum):
    """Supported filter data types"""
    STRING = "string"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    LIST = "list"
    NUMBER = "number"
    MULTISELECT = "multiselect"


class FilterCategory(str, Enum):
    """Filter categories"""
    SYNC = "sync"          # Applied at API level (what to fetch)
    INDEXING = "indexing"  # Applied at record level (what to index)


class OptionSourceType(str, Enum):
    """How filter options are provided"""
    MANUAL = "manual"      # User types values (LIST/TAGS)
    STATIC = "static"      # Predefined options (MULTISELECT with options list)
    DYNAMIC = "dynamic"    # Fetched via API call (MULTISELECT/LIST with get_filter_options)

@dataclass
class FilterOption:
    """
    Standard format for filter option values.

    Used for MULTISELECT and LIST filters with static or dynamic options.

    Args:
        id: Unique identifier (e.g., space ID, channel ID) - value stored in filter
        label: Display text shown in UI (e.g., space name, channel display name)
    """
    id: str
    label: str

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "label": self.label
        }


@dataclass
class FilterOptionsResponse:
    """
    Response format for filter options API.

    Args:
        success: Whether the request was successful
        options: List of filter options
        page: Current page number
        limit: Number of items per page
        has_more: Whether more results are available
        cursor: Optional cursor for cursor-based pagination
        message: Optional error or info message
    """
    success: bool
    options: List[FilterOption]
    page: int
    limit: int
    has_more: bool
    cursor: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            "success": self.success,
            "options": [opt.to_dict() for opt in self.options],
            "page": self.page,
            "limit": self.limit,
            "hasMore": self.has_more
        }
        if self.cursor is not None:
            result["cursor"] = self.cursor
        if self.message is not None:
            result["message"] = self.message
        return result


class FilterOperator:
    # String operators
    IS = "is"
    IS_NOT = "is_not"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does_not_contain"

    # Datetime operators
    LAST_7_DAYS = "last_7_days"
    LAST_14_DAYS = "last_14_days"
    LAST_30_DAYS = "last_30_days"
    LAST_90_DAYS = "last_90_days"
    LAST_180_DAYS = "last_180_days"
    LAST_365_DAYS = "last_365_days"
    IS_AFTER = "is_after"
    IS_BEFORE = "is_before"
    IS_BETWEEN = "is_between"

    # List operators
    IN = "in"
    NOT_IN = "not_in"

    # Number operators
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    GREATER_THAN = "greater_than"
    EQUAL = "equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"


# Type-specific operator enums that reference FilterOperator values
class StringOperator(str, Enum):
    """Operators for STRING type filters"""
    IS = FilterOperator.IS
    IS_NOT = FilterOperator.IS_NOT
    IS_EMPTY = FilterOperator.IS_EMPTY
    IS_NOT_EMPTY = FilterOperator.IS_NOT_EMPTY
    CONTAINS = FilterOperator.CONTAINS
    DOES_NOT_CONTAIN = FilterOperator.DOES_NOT_CONTAIN


class BooleanOperator(str, Enum):
    """Operators for BOOLEAN type filters"""
    IS = FilterOperator.IS
    IS_NOT = FilterOperator.IS_NOT


class DatetimeOperator(str, Enum):
    """Operators for DATETIME type filters"""
    LAST_7_DAYS = FilterOperator.LAST_7_DAYS
    LAST_14_DAYS = FilterOperator.LAST_14_DAYS
    LAST_30_DAYS = FilterOperator.LAST_30_DAYS
    LAST_90_DAYS = FilterOperator.LAST_90_DAYS
    LAST_180_DAYS = FilterOperator.LAST_180_DAYS
    LAST_365_DAYS = FilterOperator.LAST_365_DAYS
    IS_AFTER = FilterOperator.IS_AFTER
    IS_BEFORE = FilterOperator.IS_BEFORE
    IS_BETWEEN = FilterOperator.IS_BETWEEN


class ListOperator(str, Enum):
    """Operators for LIST type filters"""
    IN = FilterOperator.IN
    NOT_IN = FilterOperator.NOT_IN


class MultiselectOperator(str, Enum):
    """Operators for MULTISELECT type filters (select from predefined options)"""
    IN = FilterOperator.IN
    NOT_IN = FilterOperator.NOT_IN


class NumberOperator(str, Enum):
    """Operators for NUMBER type filters"""
    IS_BETWEEN = FilterOperator.IS_BETWEEN
    GREATER_THAN_OR_EQUAL = FilterOperator.GREATER_THAN_OR_EQUAL
    GREATER_THAN = FilterOperator.GREATER_THAN
    EQUAL = FilterOperator.EQUAL
    LESS_THAN = FilterOperator.LESS_THAN
    LESS_THAN_OR_EQUAL = FilterOperator.LESS_THAN_OR_EQUAL

# Combined operator type for type hints
FilterOperatorType = Union[
    StringOperator,
    BooleanOperator,
    DatetimeOperator,
    ListOperator,
    MultiselectOperator,
    NumberOperator
]


# FILTER KEYS (for runtime access with FilterCollection)
class SyncFilterKey(str, Enum):
    """
    Common sync filter keys for connector data fetching.
    These control what data is fetched from external APIs.
    """
    # Time-based filters
    MODIFIED = "modified"
    CREATED = "created"
    RECEIVED_DATE = "received_date"

    # Container/scope filters
    SPACE_KEYS = "space_keys"
    FOLDER_IDS = "folder_ids"
    FOLDERS = "folders"
    CUSTOM_FOLDERS = "custom_folders"
    PROJECT_IDS = "project_ids"
    PROJECT_KEYS = "project_keys"
    SITE_IDS = "site_ids"
    SITE_NAMES = "site_names"
    CHANNEL_IDS = "channel_ids"
    BOOK_IDS = "book_ids"
    GROUP_IDS = "group_ids"

    # Content filters
    CONTENT_STATUS = "content_status"
    FILE_EXTENSIONS = "file_extensions"
    MAX_FILE_SIZE = "max_file_size"
    PAGE_IDS = "page_ids"
    DRIVE_IDS = "drive_ids"
    PAGE_NAMES = "page_names"
    BLOGPOST_IDS = "blogpost_ids"

    # User filters
    OWNER_IDS = "owner_ids"
    CREATED_BY = "created_by"


class IndexingFilterKey(str, Enum):
    """
    Common indexing filter keys for record types.
    These control what record types get indexed (boolean filters).
    """
    # Master control for manual sync mode
    ENABLE_MANUAL_SYNC = "enable_manual_sync"

    # Container types
    SPACES = "spaces"
    FOLDERS = "folders"
    PROJECTS = "projects"
    SITES = "sites"
    CHANNELS = "channels"
    LISTS = "lists"

    # Content types
    PAGES = "pages"
    DATABASES = "databases"
    BLOGPOSTS = "blogposts"
    FILES = "files"
    DOCUMENTS = "documents"
    EMAILS = "emails"
    MAILS = "mails"
    MESSAGES = "messages"
    ISSUES = "issues"
    TICKETS = "tickets"
    GROUP_CONVERSATIONS = "group_conversations"
    TABLES = "index_stage_tables"
    VIEWS = "index_stage_views"
    STAGE_FILES = "index_stage_files"
    # Child content types (generic)
    COMMENTS = "comments"
    ATTACHMENTS = "attachments"

    # Child content types (specific to parent)
    PAGE_COMMENTS = "page_comments"
    PAGE_ATTACHMENTS = "page_attachments"
    BLOGPOST_COMMENTS = "blogpost_comments"
    BLOGPOST_ATTACHMENTS = "blogpost_attachments"
    ISSUE_COMMENTS = "issue_comments"
    ISSUE_ATTACHMENTS = "issue_attachments"

    # Knowledge base
    KNOWLEDGE_BASE = "knowledge_base"

    SHARED = "shared"
    SHARED_WITH_ME = "shared_with_me"


# Type to operators mapping (for validation and UI)
TYPE_OPERATORS: Dict[FilterType, List[str]] = {
    FilterType.STRING: [op.value for op in StringOperator],
    FilterType.BOOLEAN: [op.value for op in BooleanOperator],
    FilterType.DATETIME: [op.value for op in DatetimeOperator],
    FilterType.LIST: [op.value for op in ListOperator],
    FilterType.MULTISELECT: [op.value for op in MultiselectOperator],
    FilterType.NUMBER: [op.value for op in NumberOperator],
}


def get_operators_for_type(filter_type: FilterType) -> List[str]:
    """Get allowed operators for a filter type"""
    return TYPE_OPERATORS.get(filter_type, [])


def get_operator_enum_class(filter_type: FilterType) -> type:
    """Get the operator enum class for a filter type"""
    operator_map: Dict[FilterType, type] = {
        FilterType.STRING: StringOperator,
        FilterType.BOOLEAN: BooleanOperator,
        FilterType.DATETIME: DatetimeOperator,
        FilterType.LIST: ListOperator,
        FilterType.MULTISELECT: MultiselectOperator,
        FilterType.NUMBER: NumberOperator,
    }
    return operator_map.get(filter_type)


# FILTER FIELD - SCHEMA DEFINITION (for connector_builder decorator)
@dataclass
class FilterField:
    """
    Schema definition for a filter field (used in add_filter_field).

    This defines what the UI will show to users.

    Args:
        name: Unique identifier for the filter
        display_name: Human-readable name shown in UI
        filter_type: Type of filter (STRING, BOOLEAN, DATETIME, LIST, NUMBER, MULTISELECT)
        category: SYNC or INDEXING
        description: Help text for the field
        required: Whether the field is mandatory
        default_value: Default value for the field
        default_operator: Default operator (must be valid for filter_type)
        options: For MULTISELECT/LIST with static options (option_source_type=STATIC)
        option_source_type: How options are provided (MANUAL, STATIC, DYNAMIC)

    Value types by filter_type:
        STRING      → str
        BOOLEAN     → bool
        DATETIME    → tuple[int] or tuple[int, int] (epoch timestamps)
        LIST        → List[str] (manual tags or dynamic from API)
        MULTISELECT → List[str] (static or dynamic options)
        NUMBER      → float (supports decimals)

    Option Source Types:
        MANUAL  → User types values (default for LIST)
        STATIC  → Predefined options list (for MULTISELECT/LIST with options)
        DYNAMIC → Fetched via connector's get_filter_options() method
    """
    name: str
    display_name: str
    filter_type: FilterType
    category: FilterCategory = FilterCategory.SYNC
    description: str = ""
    required: bool = False
    default_value: FilterValue = None
    default_operator: Optional[str] = None
    options: List[str] = dataclass_field(default_factory=list)
    option_source_type: OptionSourceType = OptionSourceType.MANUAL

    def __post_init__(self) -> None:
        """Validate configuration"""

        # Auto-detect option_source_type if not explicitly set
        if self.option_source_type == OptionSourceType.MANUAL:
            if self.options:
                # Has static options defined
                self.option_source_type = OptionSourceType.STATIC

        # Validate option_source_type compatibility
        if self.option_source_type == OptionSourceType.DYNAMIC:
            if self.filter_type not in [FilterType.MULTISELECT, FilterType.LIST]:
                raise ValueError(
                    f"option_source_type=DYNAMIC only supported for MULTISELECT and LIST, "
                    f"got {self.filter_type}"
                )
        elif self.option_source_type == OptionSourceType.STATIC:
            if not self.options:
                raise ValueError(
                    "option_source_type=STATIC requires options list to be defined"
                )

    def _get_default_for_type(self) -> Union[str, bool, List[str], tuple, None]:
        """Get default value based on type"""
        defaults = {
            FilterType.STRING: "",
            FilterType.BOOLEAN: True,
            FilterType.DATETIME: None,  # Tuple will be created from dict/list when parsed
            FilterType.LIST: [],
            FilterType.MULTISELECT: [],
            FilterType.NUMBER: None,
        }
        return defaults.get(self.filter_type)

    def _get_default_operator(self) -> str:
        """Get default operator based on type"""
        defaults = {
            FilterType.STRING: StringOperator.IS.value,
            FilterType.BOOLEAN: BooleanOperator.IS.value,
            FilterType.DATETIME: DatetimeOperator.IS_AFTER.value,
            FilterType.LIST: ListOperator.IN.value,
            FilterType.MULTISELECT: MultiselectOperator.IN.value,
            FilterType.NUMBER: NumberOperator.EQUAL.value,
        }
        return defaults.get(self.filter_type, "")

    @property
    def operators(self) -> List[str]:
        """Get allowed operators for this filter type"""
        return get_operators_for_type(self.filter_type)

    def to_schema_dict(self) -> Dict[str, Any]:
        """Convert to dict for connector schema/config"""
        schema = {
            "name": self.name,
            "displayName": self.display_name,
            "description": self.description,
            "filterType": self.filter_type.value,
            "category": self.category.value,
            "required": self.required,
            "defaultValue": self.default_value,
            "defaultOperator": self.default_operator,
            "operators": self.operators,
            "optionSourceType": self.option_source_type.value,
        }

        if self.options:
            schema["options"] = self.options

        return schema


# RUNTIME FILTER MODELS (parsed from config)
class Filter(BaseModel):
    """
    Runtime filter value parsed from config.

    Structure in config:
        {
            "key": "space_keys",
            "value": ["DOCS", "ENG"],
            "type": "list",
            "operator": "in"
        }
    """
    key: str
    value: FilterValue = None
    type: FilterType
    operator: FilterOperatorType

    @model_validator(mode='before')
    @classmethod
    def convert_operator_to_enum(cls, data: object) -> dict[str, object]:
        """Convert string operator to appropriate enum and normalize datetime values"""
        if isinstance(data, dict) and 'operator' in data and 'type' in data:
            operator_str = data['operator']
            filter_type_str = data['type']

            # If operator is already an enum, skip conversion
            if isinstance(operator_str, (StringOperator, BooleanOperator, DatetimeOperator, ListOperator, MultiselectOperator, NumberOperator)):
                pass  # Continue to datetime value conversion
            else:
                # Convert type string to enum if needed
                if isinstance(filter_type_str, str):
                    try:
                        filter_type = FilterType(filter_type_str)
                    except ValueError:
                        return data  # Will be caught in validation
                else:
                    filter_type = filter_type_str

                # Handle legacy operators (backward compatibility)
                operator_migrations = {
                    'equals': {
                        FilterType.BOOLEAN: 'is',
                        FilterType.NUMBER: 'equal',
                        FilterType.STRING: 'is',
                    }
                }

                if isinstance(operator_str, str) and operator_str in operator_migrations:
                    migration_map = operator_migrations[operator_str]
                    if filter_type in migration_map:
                        operator_str = migration_map[filter_type]
                        data['operator'] = operator_str

                # Convert operator string to enum
                if isinstance(operator_str, str):
                    operator_enum_class = get_operator_enum_class(filter_type)
                    if operator_enum_class:
                        try:
                            # Try to find the enum by value
                            for op in operator_enum_class:
                                if op.value == operator_str:
                                    data['operator'] = op
                                    break
                        except (ValueError, AttributeError):
                            pass

            # Convert datetime value to tuple of epoch integers
            # Also extract id from {id, label} objects for LIST and MULTISELECT filters
            if 'value' in data and 'type' in data:
                filter_type_str = data['type']
                if isinstance(filter_type_str, str):
                    try:
                        filter_type = FilterType(filter_type_str)
                    except ValueError:
                        return data
                else:
                    filter_type = filter_type_str

                if filter_type == FilterType.DATETIME and data['value'] is not None:
                    value = data['value']
                    # Convert dict {start, end} to tuple of epoch integers (milliseconds)
                    if isinstance(value, dict):
                        start = value.get('start')
                        end = value.get('end')
                        # Convert to integers (epoch in milliseconds)
                        start = int(start) if start is not None else None
                        end = int(end) if end is not None else None
                        data['value'] = (start, end)
                    else:
                        data['value'] = None
                elif filter_type in (FilterType.LIST, FilterType.MULTISELECT) and data['value'] is not None:
                    value = data['value']
                    # Extract id from {id, label} objects if present
                    if isinstance(value, list):
                        extracted_ids = []
                        for item in value:
                            if isinstance(item, dict) and 'id' in item:
                                # New format: {id, label} object - extract id
                                extracted_ids.append(item['id'])
                            elif isinstance(item, str):
                                # Old format: string id (backward compatibility)
                                extracted_ids.append(item)
                            else:
                                # Fallback: convert to string
                                extracted_ids.append(str(item))
                        data['value'] = extracted_ids

        return data

    @model_validator(mode='after')
    def validate_filter(self) -> 'Filter':
        """Validate key, operator, and value are valid for the filter type"""
        # Validate key is non-empty
        if not self.key or not self.key.strip():
            raise ValueError("Filter key cannot be empty")

        # Convert operator to enum if it's still a string (fallback)
        if isinstance(self.operator, str):
            operator_enum_class = get_operator_enum_class(self.type)
            if operator_enum_class:
                try:
                    # Try to find the enum by value
                    for op in operator_enum_class:
                        if op.value == self.operator:
                            self.operator = op
                            break
                    else:
                        # Operator not found in enum
                        valid_operators = TYPE_OPERATORS.get(self.type, [])
                        raise ValueError(
                            f"Invalid operator '{self.operator}' for type '{self.type.value}'. "
                            f"Valid operators: {valid_operators}"
                        )
                except (ValueError, AttributeError) as e:
                    valid_operators = TYPE_OPERATORS.get(self.type, [])
                    raise ValueError(
                        f"Invalid operator '{self.operator}' for type '{self.type.value}'. "
                        f"Valid operators: {valid_operators}"
                    ) from e

        # Validate operator is valid for type
        valid_operators = TYPE_OPERATORS.get(self.type, [])
        operator_value = self.operator.value if isinstance(self.operator, Enum) else str(self.operator)
        if operator_value not in valid_operators:
            raise ValueError(
                f"Invalid operator '{operator_value}' for type '{self.type.value}'. "
                f"Valid operators: {valid_operators}"
            )

        # Validate value type (if value is set)
        if self.value is not None:
            if self.type == FilterType.DATETIME:
                # Datetime values are tuples: (start, end) where each is epoch ms or None
                if not isinstance(self.value, tuple):
                    raise ValueError(
                        f"Invalid value type for '{self.type.value}': "
                        f"expected tuple, got {type(self.value).__name__}"
                    )
                # Validate tuple length is exactly 2
                if len(self.value) != MAX_DATETIME_TUPLE_LENGTH:
                    raise ValueError(
                        f"Invalid datetime tuple length: expected 2 elements (start, end), got {len(self.value)}"
                    )
                # Validate tuple elements are integers (epoch ms) or None
                for i, item in enumerate(self.value):
                    if item is not None and not isinstance(item, int):
                        raise ValueError(
                            f"Invalid datetime tuple item at index {i}: expected int (epoch ms), got {type(item).__name__}"
                        )
            else:
                expected_types: Dict[FilterType, type | tuple] = {
                    FilterType.STRING: str,
                    FilterType.BOOLEAN: bool,
                    FilterType.LIST: list,
                    FilterType.MULTISELECT: list,
                    FilterType.NUMBER: (int, float),
                }
                expected = expected_types.get(self.type)
                if expected and not isinstance(self.value, expected):
                    raise ValueError(
                        f"Invalid value type for '{self.type.value}': "
                        f"expected {expected}, got {type(self.value).__name__}"
                    )

            # For LIST and MULTISELECT types, validate all elements are strings
            if self.type in (FilterType.LIST, FilterType.MULTISELECT):
                for i, item in enumerate(self.value):
                    if not isinstance(item, str):
                        raise ValueError(
                            f"Invalid list item at index {i}: expected str, got {type(item).__name__}"
                        )

        return self

    def is_empty(self) -> bool:
        """Check if filter value is empty/not set"""
        if self.value is None:
            return True
        if isinstance(self.value, str) and self.value == "":
            return True
        if isinstance(self.value, list) and len(self.value) == 0:
            return True
        if isinstance(self.value, tuple) and len(self.value) == 0:
            return True
        return False

    def as_list(self) -> List[Any]:
        """Get value as list (wraps single values)"""
        if isinstance(self.value, list):
            return self.value
        return [self.value] if self.value is not None else []

    def get_value(self, default: Optional[FilterValue] = None) -> FilterValue:
        """
        Get filter value (raw).

        For datetime filters, returns tuple (start, end) in epoch milliseconds:
        - IS_AFTER: (start_epoch, None)
        - IS_BEFORE: (None, end_epoch)
        - IS_BETWEEN: (start_epoch, end_epoch)

        Args:
            default: Default value to return if value is None or empty (default: None)

        Returns:
            Filter value, or default if value is empty/None
            For datetime filters: tuple[int | None, int | None] (epoch in milliseconds)
        """
        if self.is_empty():
            return default

        return self.value

    def get_operator(self) -> FilterOperatorType:
        """
        Get filter operator as enum.

        Returns:
            FilterOperatorType enum instance (Union of all operator enums)
        """
        return self.operator

    def get_datetime_start(self) -> Optional[int]:
        """
        Get start epoch from datetime filter value.

        Returns:
            Start epoch timestamp (milliseconds), or None if not a datetime filter or value is empty
        """
        if self.type != FilterType.DATETIME or self.is_empty():
            return None
        if isinstance(self.value, tuple) and len(self.value) > 0:
            return self.value[0]
        return None

    def get_datetime_end(self) -> Optional[int]:
        """
        Get end epoch from datetime filter value.

        Returns:
            End epoch timestamp (milliseconds), or None if not a datetime filter, value is empty, or no end date
        """
        if self.type != FilterType.DATETIME or self.is_empty():
            return None
        if isinstance(self.value, tuple) and len(self.value) > 1:
            return self.value[1]
        return None

    @staticmethod
    def _epoch_to_iso(epoch: Optional[int]) -> Optional[str]:
        """Convert epoch timestamp (milliseconds) to ISO format string.

        Args:
            epoch: Epoch timestamp in milliseconds

        Returns:
            ISO format string (e.g., "2025-12-04T10:30:00")
        """
        if epoch is None:
            return None
        # Convert milliseconds to seconds
        epoch_seconds = epoch / 1000
        dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    def get_datetime_iso(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get datetime filter value as ISO format strings.

        Value format from JSON is always {start, end} → tuple (start, end)
        - IS_AFTER: {start: epoch, end: None} → (start_iso, None)
        - IS_BEFORE: {start: None, end: epoch} → (None, end_iso)
        - IS_BETWEEN: {start: epoch, end: epoch} → (start_iso, end_iso)

        Returns:
            Tuple of (start_iso, end_iso) where each is an ISO format string or None.
            Returns (None, None) if not a datetime filter or value is empty.
        """
        if self.type != FilterType.DATETIME or self.is_empty():
            return (None, None)

        start_epoch = self.value[0]
        end_epoch = self.value[1]

        return (self._epoch_to_iso(start_epoch), self._epoch_to_iso(end_epoch))

    @property
    def operator_value(self) -> str:
        """Get operator as string value"""
        return self.operator.value if isinstance(self.operator, Enum) else str(self.operator)

class FilterCollection(BaseModel):
    """
    Collection of filters with easy access methods.

    Used for both sync filters and indexing filters.

    Main methods:
        - get(key) → Optional[Filter]: Get full filter object
        - get_value(key, default) → Any: Get value, None if empty
        - is_enabled(key, default=True) → bool: For boolean filters
    """
    filters: List[Filter] = Field(default_factory=list)

    def get(self, key: Union[str, Enum]) -> Optional[Filter]:
        """
        Get filter by key.

        Returns None if not found.
        Use this when you need access to operator or type.
        """
        key_str = key.value if isinstance(key, Enum) else key
        for f in self.filters:
            if f.key == key_str:
                return f
        return None

    def get_value(
        self, key: Union[str, Enum], default: Optional[FilterValue] = None
    ) -> FilterValue:
        """
        Get filter value.

        Returns default (None) if:
        - Filter doesn't exist
        - Value is None
        - Value is empty list []
        - Value is empty string ""

        This allows simple truthy checks in connectors:
            space_keys = filters.get_value(SyncFilterKey.SPACE_KEYS)
            if space_keys:
                # Apply filter
        """
        f = self.get(key)
        if f is None or f.is_empty():
            return default
        return f.value

    def is_enabled(self, key: Union[str, Enum], default: Optional[bool] = True) -> bool:
        """
        Check if boolean filter is enabled.

        Args:
            key: Filter key (string or enum)
            default: Value if filter not found (default: True = enabled)

        Returns:
            - True if filter value is truthy (True, non-empty string/list)
            - False if filter value is False
            - default if filter not found or empty

        Use for indexing filters:
            if not indexing_filters.is_enabled(IndexingFilterKey.PAGES):
                record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

        Note: For non-boolean filters, this uses Python's truthiness rules:
            - Lists: True if non-empty
            - Strings: True if non-empty
            - Numbers: True if non-zero
        """
        # Get the filter being checked
        f = self.get(key)

        # Convert key to string for comparison
        key_str = key.value if isinstance(key, Enum) else key

        # If checking enable_manual_sync itself, return its value directly
        if key_str == "enable_manual_sync":
            if f is None:
                return default
            if f.is_empty():
                return default
            if f.type == FilterType.BOOLEAN:
                return bool(f.value)
            return True

        # For other indexing filters, check if manual sync is enabled
        # Only apply override if enable_manual_sync filter exists and is explicitly enabled
        manual_sync_filter = self.get("enable_manual_sync")
        if (manual_sync_filter is not None
            and not manual_sync_filter.is_empty()
            and manual_sync_filter.type == FilterType.BOOLEAN
            and manual_sync_filter.value is True):
            # Manual sync is explicitly ON: no auto-indexing
            return False

        # Normal logic: return the specific filter's value
        if f is None:
            return default
        if f.is_empty():
            return default
        if f.type == FilterType.BOOLEAN:
            return bool(f.value)
        return True  # Non-empty non-boolean = enabled

    def keys(self) -> List[str]:
        """Get all filter keys"""
        return [f.key for f in self.filters]

    def __len__(self) -> int:
        return len(self.filters)

    def __bool__(self) -> bool:
        """Returns True if there are any filters"""
        return len(self.filters) > 0

    @classmethod
    def from_dict(
        cls,
        filter_dict: Dict[str, Any],
        logger: Optional[Logger] = None
    ) -> 'FilterCollection':
        """
        Create FilterCollection from config dict.

        Args:
            filter_dict: Dict of filter configurations (pass empty dict if no filters)
            logger: Optional logger for warnings (uses module logger if not provided)

        Expected format:
            {
                "space_keys": {"value": ["DOCS"], "operator": "in", "type": "list"},
                "modified_after": {"value": "2024-01-01", "operator": "is_after", "type": "datetime"}
            }

        Returns:
            FilterCollection with valid filters. Invalid filters are logged and skipped.
        """
        log = logger or _logger

        if not filter_dict:
            return cls()

        filters: List[Filter] = []
        for key, val in filter_dict.items():
            try:
                # Validate filter structure
                if not isinstance(val, dict):
                    log.warning(f"Skipping filter: expected dict, got {type(val).__name__}")
                    continue

                if "operator" not in val or "type" not in val:
                    log.warning("Skipping filter: missing required 'operator' or 'type'")
                    continue

                # Model validator handles key, operator, and value validation
                filter_data = {"key": key, **val}
                filters.append(Filter.model_validate(filter_data))

            except ValueError as e:
                log.warning(f"Invalid filter: {e}")
                continue

        return cls(filters=filters)


async def load_connector_filters(
    config_service: ConfigurationService,
    connector_name: str,
    connector_id: str,
    logger: Optional[Logger] = None
) -> Tuple[FilterCollection, FilterCollection]:
    """
    Load sync and indexing filters from config service.

    Args:
        config_service: ConfigurationService instance
        connector_name: Name of connector (e.g., "confluence", "outlook")
        connector_id: ID of connector (e.g., "confluence_123", "outlook_456")
        logger: Optional logger (uses module logger if not provided)

    Returns:
        Tuple of (sync_filters, indexing_filters)
        Returns empty collections if config not found or on error.

    Expected config structure:
        {
            "filters": {
                "sync": {
                    "values": {
                        "space_keys": {"operator": "in", "value": ["DOCS"]},
                        "modified_after": {"operator": "is_after", "value": "2024-01-01"}
                    }
                },
                "indexing": {
                    "values": {
                        "pages": {"operator": "is", "value": true}
                    }
                }
            }
        }
    """
    log = logger or _logger
    empty_filters = (FilterCollection(), FilterCollection())
    config_path = f"/services/connectors/{connector_id}/config"

    # Fetch config from service
    try:
        config = await config_service.get_config(config_path)
    except Exception as e:
        log.error(f"Failed to fetch config for {connector_id}: {e}")
        return empty_filters

    # Handle missing or disabled config
    if not config:
        log.debug(f"No config found for {connector_name} {connector_id}")
        return empty_filters

    if not config.get("enabled", True):
        log.debug(f"Connector {connector_name} {connector_id} is disabled")
        return empty_filters

    # Extract filter values from config
    filters_config = config.get("filters", {})
    if not filters_config:
        log.debug(f"No filters configured for {connector_name} {connector_id}")
        return empty_filters

    sync_values = filters_config.get("sync", {}).get("values", {})
    indexing_values = filters_config.get("indexing", {}).get("values", {})

    # Parse filters (from_dict logs warnings for invalid filters)
    sync_filters = FilterCollection.from_dict(sync_values, log)
    indexing_filters = FilterCollection.from_dict(indexing_values, log)

    # Log summary
    if sync_filters or indexing_filters:
        log.info(
            f"Loaded filters for {connector_name} {connector_id}: "
            f"{len(sync_filters)} sync, {len(indexing_filters)} indexing"
        )

    return sync_filters, indexing_filters
