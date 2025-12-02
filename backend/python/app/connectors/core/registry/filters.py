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
from enum import Enum
from logging import Logger
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator

from app.config.configuration_service import ConfigurationService

# Module logger for filter parsing warnings
_logger = logging.getLogger(__name__)

# Type alias for filter values (string, bool, list, number, or None)
FilterValue = Union[str, bool, int, float, List[str], None]
MAX_DATETIME_TUPLE_LENGTH = 2


class FilterType(str, Enum):
    """Supported filter data types"""
    STRING = "string"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    LIST = "list"
    NUMBER = "number"


class FilterCategory(str, Enum):
    """Filter categories"""
    SYNC = "sync"          # Applied at API level (what to fetch)
    INDEXING = "indexing"  # Applied at record level (what to index)


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
    GREATER_THAN_EQUAL = "gte"
    GREATER_THAN = "gt"
    EQUAL = "eq"
    LESS_THAN = "lt"
    LESS_THAN_EQUAL = "lte"


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


class NumberOperator(str, Enum):
    """Operators for NUMBER type filters"""
    IS_BETWEEN = FilterOperator.IS_BETWEEN
    GREATER_THAN_EQUAL = FilterOperator.GREATER_THAN_EQUAL
    GREATER_THAN = FilterOperator.GREATER_THAN
    EQUAL = FilterOperator.EQUAL
    LESS_THAN = FilterOperator.LESS_THAN
    LESS_THAN_EQUAL = FilterOperator.LESS_THAN_EQUAL

# Combined operator type for type hints
FilterOperatorType = Union[
    StringOperator,
    BooleanOperator,
    DatetimeOperator,
    ListOperator,
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

    # Container/scope filters
    SPACE_KEYS = "space_keys"
    FOLDER_IDS = "folder_ids"
    PROJECT_IDS = "project_ids"
    SITE_IDS = "site_ids"
    CHANNEL_IDS = "channel_ids"

    # Content filters
    CONTENT_STATUS = "content_status"
    FILE_EXTENSIONS = "file_extensions"
    MAX_FILE_SIZE = "max_file_size"

    # User filters
    OWNER_IDS = "owner_ids"
    CREATED_BY = "created_by"


class IndexingFilterKey(str, Enum):
    """
    Common indexing filter keys for record types.
    These control what record types get indexed (boolean filters).
    """
    # Container types
    SPACES = "spaces"
    FOLDERS = "folders"
    PROJECTS = "projects"
    SITES = "sites"
    CHANNELS = "channels"

    # Content types
    PAGES = "pages"
    BLOGPOSTS = "blogposts"
    FILES = "files"
    DOCUMENTS = "documents"
    EMAILS = "emails"
    MESSAGES = "messages"
    ISSUES = "issues"
    TICKETS = "tickets"

    # Child content types (generic)
    COMMENTS = "comments"
    ATTACHMENTS = "attachments"

    # Child content types (specific to parent)
    PAGE_COMMENTS = "page_comments"
    PAGE_ATTACHMENTS = "page_attachments"
    BLOGPOST_COMMENTS = "blogpost_comments"
    BLOGPOST_ATTACHMENTS = "blogpost_attachments"


# Type to operators mapping (for validation and UI)
TYPE_OPERATORS: Dict[FilterType, List[str]] = {
    FilterType.STRING: [op.value for op in StringOperator],
    FilterType.BOOLEAN: [op.value for op in BooleanOperator],
    FilterType.DATETIME: [op.value for op in DatetimeOperator],
    FilterType.LIST: [op.value for op in ListOperator],
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
        filter_type: Type of filter (STRING, BOOLEAN, DATETIME, LIST, NUMBER)
        category: SYNC or INDEXING
        description: Help text for the field
        required: Whether the field is mandatory
        default_value: Default value for the field
        default_operator: Default operator (must be valid for filter_type)
        options: For LIST type, predefined options to select from

    Value types by filter_type:
        STRING   → str
        BOOLEAN  → bool
        DATETIME → tuple[str] or tuple[str, str] (single date or (start, end) for between)
        LIST     → List[str]
        NUMBER   → float (supports decimals)
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

    def __post_init__(self) -> None:
        """Set default values based on filter_type"""
        if self.default_value is None:
            self.default_value = self._get_default_for_type()
        if self.default_operator is None:
            self.default_operator = self._get_default_operator()

    def _get_default_for_type(self) -> Union[str, bool, List[str], tuple, None]:
        """Get default value based on type"""
        defaults = {
            FilterType.STRING: "",
            FilterType.BOOLEAN: True,
            FilterType.DATETIME: None,  # Tuple will be created from dict/list when parsed
            FilterType.LIST: [],
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
            if isinstance(operator_str, (StringOperator, BooleanOperator, DatetimeOperator, ListOperator, NumberOperator)):
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

            # Convert datetime value from dict/list to tuple
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
                    # Convert dict {start:, end:} to tuple
                    if isinstance(value, dict):
                        start = value.get('start')
                        end = value.get('end')
                        # Create tuple: (start, end) or (start,) if end is None
                        if start is not None:
                            data['value'] = (start, end) if end is not None else (start,)
                        else:
                            data['value'] = None
                    elif isinstance(value, list):
                        if len(value) > 0:
                            data['value'] = tuple(value) if len(value) > 1 else (value[0],)
                        else:
                            data['value'] = None
                    elif isinstance(value, tuple):
                        pass
                    elif isinstance(value, str):
                        data['value'] = (value,)

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
                # Datetime values should be tuples: (start,) or (start, end)
                if not isinstance(self.value, tuple):
                    raise ValueError(
                        f"Invalid value type for '{self.type.value}': "
                        f"expected tuple, got {type(self.value).__name__}"
                    )
                # Validate tuple elements are strings
                for i, item in enumerate(self.value):
                    if not isinstance(item, str):
                        raise ValueError(
                            f"Invalid datetime tuple item at index {i}: expected str, got {type(item).__name__}"
                        )
                # Validate tuple length (1 or 2 elements)
                if len(self.value) > MAX_DATETIME_TUPLE_LENGTH:
                    raise ValueError(
                        f"Invalid datetime tuple length: expected 1 or 2 elements, got {len(self.value)}"
                    )
            else:
                expected_types: Dict[FilterType, type | tuple] = {
                    FilterType.STRING: str,
                    FilterType.BOOLEAN: bool,
                    FilterType.LIST: list,
                    FilterType.NUMBER: (int, float),
                }
                expected = expected_types.get(self.type)
                if expected and not isinstance(self.value, expected):
                    raise ValueError(
                        f"Invalid value type for '{self.type.value}': "
                        f"expected {expected}, got {type(self.value).__name__}"
                    )

            # For LIST type, validate all elements are strings
            if self.type == FilterType.LIST:
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

    def get_value(self, default: FilterValue) -> FilterValue:
        """
        Get filter value.

        For datetime filters, returns normalized tuple (start, end) based on operator:
        - IS_AFTER: (start_date, None) - start has value, end is None
        - IS_BEFORE: (None, start_date) - start is None, end has value
        - IS_BETWEEN: (start_date, end_date) - both have values

        Args:
            default: Default value to return if value is None or empty

        Returns:
            Filter value, or default if value is empty/None
            For datetime filters: tuple[str | None, str | None]
        """
        if self.is_empty():
            return default

        # Normalize datetime values based on operator
        if self.type == FilterType.DATETIME and isinstance(self.value, tuple):
            operator = self.get_operator()
            start_date = self.value[0] if len(self.value) > 0 else None
            end_date = self.value[1] if len(self.value) > 1 else None

            if operator == DatetimeOperator.IS_AFTER:
                # IS_AFTER: (start_date, None)
                return (start_date, None)
            elif operator == DatetimeOperator.IS_BEFORE:
                # IS_BEFORE: (None, start_date) - the start_date becomes the "before" value
                return (None, start_date)
            elif operator == DatetimeOperator.IS_BETWEEN:
                # IS_BETWEEN: (start_date, end_date)
                return (start_date, end_date)
            else:
                # For other operators, return as-is
                return self.value

        return self.value

    def get_operator(self) -> FilterOperatorType:
        """
        Get filter operator as enum.

        Returns:
            FilterOperatorType enum instance (Union of all operator enums)
        """
        return self.operator

    def get_datetime_start(self) -> Optional[str]:
        """
        Get start date from datetime filter value.

        Returns:
            Start date string, or None if not a datetime filter or value is empty
        """
        if self.type != FilterType.DATETIME or self.is_empty():
            return None
        if isinstance(self.value, tuple) and len(self.value) > 0:
            return self.value[0]
        return None

    def get_datetime_end(self) -> Optional[str]:
        """
        Get end date from datetime filter value.

        Returns:
            End date string, or None if not a datetime filter, value is empty, or no end date
        """
        if self.type != FilterType.DATETIME or self.is_empty():
            return None
        if isinstance(self.value, tuple) and len(self.value) > 1:
            return self.value[1]
        return None

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
        self, key: Union[str, Enum], default: FilterValue = None
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

    def is_enabled(self, key: Union[str, Enum], default: bool = True) -> bool:
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
        f = self.get(key)
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
                    log.warning(f"Skipping filter '{key}': expected dict, got {type(val).__name__}")
                    continue

                if "operator" not in val or "type" not in val:
                    log.warning(f"Skipping filter '{key}': missing required 'operator' or 'type'")
                    continue

                # Model validator handles key, operator, and value validation
                filter_data = {"key": key, **val}
                filters.append(Filter.model_validate(filter_data))

            except ValueError as e:
                log.warning(f"Invalid filter '{key}': {e}")
                continue

        return cls(filters=filters)


async def load_connector_filters(
    config_service: ConfigurationService,
    connector_name: str,
    logger: Optional[Logger] = None
) -> Tuple[FilterCollection, FilterCollection]:
    """
    Load sync and indexing filters from config service.

    Args:
        config_service: ConfigurationService instance
        connector_name: Name of connector (e.g., "confluence", "outlook")
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
    config_path = f"/services/connectors/{connector_name.lower()}/config"

    # Fetch config from service
    try:
        config = await config_service.get_config(config_path)
    except Exception as e:
        log.error(f"Failed to fetch config for {connector_name}: {e}")
        return empty_filters

    # Handle missing or disabled config
    if not config:
        log.debug(f"No config found for {connector_name}")
        return empty_filters

    if not config.get("enabled", True):
        log.debug(f"Connector {connector_name} is disabled")
        return empty_filters

    # Extract filter values from config
    filters_config = config.get("filters", {})
    if not filters_config:
        log.debug(f"No filters configured for {connector_name}")
        return empty_filters

    sync_values = filters_config.get("sync", {}).get("values", {})
    indexing_values = filters_config.get("indexing", {}).get("values", {})

    # Parse filters (from_dict logs warnings for invalid filters)
    sync_filters = FilterCollection.from_dict(sync_values, log)
    indexing_filters = FilterCollection.from_dict(indexing_values, log)

    # Log summary
    if sync_filters or indexing_filters:
        log.info(
            f"Loaded filters for {connector_name}: "
            f"{len(sync_filters)} sync, {len(indexing_filters)} indexing"
        )

    return sync_filters, indexing_filters
