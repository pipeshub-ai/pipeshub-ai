"""
Filter Types, Operators, and Models for Connector Filtering System.

This module provides:
1. FilterType - Supported filter data types with fixed operators per type
2. FilterCategory - SYNC (API-level) vs INDEXING (record-level)
3. SyncFilterKey / IndexingFilterKey - Common filter keys for runtime access
4. FilterField - Schema definition for UI (used in add_filter_field decorator)
5. Filter/FilterCollection - Runtime filter parsing from config

Usage in connector decorator:
    from app.connectors.core.registry.filters import FilterField, FilterType, FilterCategory

    @ConnectorBuilder("Confluence")
        .configure(lambda builder: builder
            .add_filter_field(FilterField(
                name="space_keys",
                display_name="Space Keys",
                filter_type=FilterType.LIST,
                category=FilterCategory.SYNC,
            ))
            .add_filter_field(FilterField(
                name="pages",
                display_name="Index Pages",
                filter_type=FilterType.BOOLEAN,
                category=FilterCategory.INDEXING,
                default_value=True,
            ))
        )

Usage at runtime:
    from app.connectors.core.registry.filters import (
        load_connector_filters, SyncFilterKey, IndexingFilterKey
    )

    sync_filters, indexing_filters = await load_connector_filters(
        config_service, "confluence", logger
    )

    # Sync filters - get value (returns None if empty/not set)
    space_keys = sync_filters.get_value(SyncFilterKey.SPACE_KEYS)
    if space_keys:
        # Apply filter
        for key in space_keys:
            await api.get_space(key=key)

    # For datetime/number filters that need operator access
    modified_filter = sync_filters.get(SyncFilterKey.MODIFIED_AFTER)
    if modified_filter:
        operator = modified_filter.operator
        start, end = modified_filter.get_datetime_values()

    # Indexing filters - boolean check (default=True if not set)
    if not indexing_filters.is_enabled(IndexingFilterKey.PAGES):
        record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
"""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from logging import Logger
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService

# Type alias for filter values (string, bool, list, number, or None)
FilterValue = Union[str, bool, int, float, List[str], None]


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


# OPERATORS BY TYPE
class StringOperator(str, Enum):
    """Operators for STRING type filters"""
    IS = "is"
    IS_NOT = "is_not"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    CONTAINS = "contains"
    DOES_NOT_CONTAIN = "does_not_contain"


class BooleanOperator(str, Enum):
    """Operators for BOOLEAN type filters"""
    IS = "is"
    IS_NOT = "is_not"


class DatetimeOperator(str, Enum):
    """Operators for DATETIME type filters"""
    LAST_7_DAYS = "last_7_days"
    LAST_14_DAYS = "last_14_days"
    LAST_30_DAYS = "last_30_days"
    LAST_90_DAYS = "last_90_days"
    LAST_180_DAYS = "last_180_days"
    LAST_365_DAYS = "last_365_days"
    EMPTY = "empty"
    IS = "is"
    IS_AFTER = "is_after"
    IS_ON_OR_AFTER = "is_on_or_after"
    IS_ON_OR_BEFORE = "is_on_or_before"
    IS_BEFORE = "is_before"
    IS_BETWEEN = "is_between"


class ListOperator(str, Enum):
    """Operators for LIST type filters"""
    IN = "in"
    NOT_IN = "not_in"


class NumberOperator(str, Enum):
    """Operators for NUMBER type filters"""
    IS_BETWEEN = "is_between"
    GREATER_THAN_EQUAL = "gte"
    GREATER_THAN = "gt"
    EQUAL = "eq"
    LESS_THAN = "lt"
    LESS_THAN_EQUAL = "lte"


# Combined operator type for type hints
FilterOperator = Union[
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
    MODIFIED_AFTER = "modified_after"
    MODIFIED_BEFORE = "modified_before"
    CREATED_AFTER = "created_after"
    CREATED_BEFORE = "created_before"

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
        options_endpoint: API endpoint to fetch options dynamically

    Value types by filter_type:
        STRING   → str
        BOOLEAN  → bool
        DATETIME → List[str] (1 date, 2 for between, or empty)
        LIST     → List[str]
        NUMBER   → float (supports decimals)
    """
    name: str
    display_name: str
    filter_type: FilterType
    category: FilterCategory = FilterCategory.SYNC
    description: str = ""
    required: bool = False
    default_value: Any = None
    default_operator: Optional[str] = None
    options: List[str] = dataclass_field(default_factory=list)
    options_endpoint: Optional[str] = None

    def __post_init__(self) -> None:
        """Set default values based on filter_type"""
        if self.default_value is None:
            self.default_value = self._get_default_for_type()
        if self.default_operator is None:
            self.default_operator = self._get_default_operator()

    def _get_default_for_type(self) -> Union[str, bool, List[str], None]:
        """Get default value based on type"""
        defaults = {
            FilterType.STRING: "",
            FilterType.BOOLEAN: True,
            FilterType.DATETIME: [],
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
        if self.options_endpoint:
            schema["optionsEndpoint"] = self.options_endpoint

        return schema


# RUNTIME FILTER MODELS (parsed from config)
class Filter(BaseModel):
    """
    Runtime filter value parsed from config.

    Structure in config:
        {
            "key": "space_keys",
            "value": ["DOCS", "ENG"],
            "type": "multiselect",
            "operator": "in"
        }

    Access value directly via .value property.
    Use helper methods for type-specific processing.
    """
    key: str
    value: Any
    type: FilterType = FilterType.STRING
    operator: Optional[str] = None

    def is_empty(self) -> bool:
        """Check if filter value is empty/not set"""
        if self.value is None:
            return True
        if isinstance(self.value, str) and self.value == "":
            return True
        if isinstance(self.value, list) and len(self.value) == 0:
            return True
        return False

    def as_list(self) -> List[Any]:
        """Get value as list (wraps single values)"""
        if isinstance(self.value, list):
            return self.value
        return [self.value] if self.value is not None else []

    def get_datetime_values(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Get datetime values for operators that need date range.
        Returns (start_date, end_date)
        """
        values = self.as_list()
        start = values[0] if len(values) > 0 else None
        end = values[1] if len(values) > 1 else None
        return start, end

    def get_number_range(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Get number range for between operator.
        Returns (min_value, max_value)
        """
        values = self.as_list()
        min_val = float(values[0]) if len(values) > 0 and values[0] is not None else None
        max_val = float(values[1]) if len(values) > 1 and values[1] is not None else None
        return min_val, max_val


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

    # ACCESS METHODS
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

        Use for indexing filters:
            if not indexing_filters.is_enabled(IndexingFilterKey.PAGES):
                record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
        """
        f = self.get(key)
        if f is None:
            return default
        # Treat empty as default (not configured = use default)
        if f.is_empty():
            return default
        return bool(f.value)

    def __contains__(self, key: Union[str, Enum]) -> bool:
        """Check if filter exists: 'space_keys' in filters"""
        return self.get(key) is not None

    def __getitem__(self, key: Union[str, Enum]) -> Filter:
        """Get filter: filters['space_keys'] - raises KeyError if not found"""
        f = self.get(key)
        if f is None:
            key_str = key.value if isinstance(key, Enum) else key
            raise KeyError(f"No filter with key: {key_str}")
        return f

    def keys(self) -> List[str]:
        """Get all filter keys"""
        return [f.key for f in self.filters]

    def __len__(self) -> int:
        return len(self.filters)

    def __iter__(self) -> Iterator[Filter]:
        return iter(self.filters)

    def __bool__(self) -> bool:
        """Returns True if there are any filters"""
        return len(self.filters) > 0

    @classmethod
    def from_list(cls, filter_list: Optional[List[Dict[str, Any]]]) -> 'FilterCollection':
        """
        Create FilterCollection from list of filter dicts.

        Args:
            filter_list: List of filter definitions
                [{"key": "space_keys", "value": ["123"], "type": "multiselect", "operator": "in"}]
        """
        if not filter_list:
            return cls()

        filters = []
        for f in filter_list:
            try:
                filters.append(Filter(**f))
            except Exception:
                continue  # Skip invalid filters

        return cls(filters=filters)

    @classmethod
    def from_dict(cls, filter_dict: Optional[Dict[str, Any]]) -> 'FilterCollection':
        """
        Create from dict of {key: value} or {key: {value, operator, type}}.

        Supports both simple and full formats:
            Simple: {"space_keys": ["DOCS"]}
            Full: {"space_keys": {"value": ["DOCS"], "operator": "in", "type": "multiselect"}}
        """
        if not filter_dict:
            return cls()

        filters = []
        for key, val in filter_dict.items():
            try:
                if isinstance(val, dict) and "value" in val:
                    # Full format
                    filters.append(Filter(key=key, **val))
                else:
                    # Simple format
                    filters.append(Filter(key=key, value=val))
            except Exception:
                continue

        return cls(filters=filters)


# LOAD FILTERS FROM CONFIG SERVICE
async def load_connector_filters(
    config_service: ConfigurationService,
    connector_name: str,
    logger: Optional[Logger] = None
) -> Tuple[FilterCollection, FilterCollection]:
    """
    Load sync and indexing filters from config service.

    Filters should be fetched fresh before each sync to get latest config.

    Args:
        config_service: ConfigurationService instance
        connector_name: Name of connector (e.g., "confluence", "outlook")
        logger: Optional logger

    Returns:
        Tuple of (sync_filters, indexing_filters)

    Expected config structure:
        {
            "enabled": true,
            "sync": {
                "filters": [
                    {"key": "space_keys", "value": ["DOCS"], "type": "list", "operator": "in"},
                    {"key": "modified_after", "value": ["2024-01-01"], "type": "datetime", "operator": "is_after"}
                ]
            },
            "indexing": {
                "filters": [
                    {"key": "pages", "value": true, "type": "boolean", "operator": "is"},
                    {"key": "comments", "value": false, "type": "boolean", "operator": "is"}
                ]
            }
        }
    """
    empty_filters = (FilterCollection(), FilterCollection())

    try:
        config = await config_service.get_config(
            f"/services/connectors/{connector_name.lower()}/filters"
        )

        if not config:
            if logger:
                logger.debug(f"No filter config found for {connector_name}")
            return empty_filters

        if not config.get("enabled", True):
            if logger:
                logger.debug(f"Filters disabled for {connector_name}")
            return empty_filters

        sync_filters = FilterCollection.from_list(
            config.get("sync", {}).get("filters", [])
        )
        indexing_filters = FilterCollection.from_list(
            config.get("indexing", {}).get("filters", [])
        )

        if logger:
            if sync_filters or indexing_filters:
                logger.info(
                    f"Loaded filters for {connector_name}: "
                    f"{len(sync_filters)} sync, {len(indexing_filters)} indexing"
                )
                if sync_filters:
                    logger.debug(f"Sync filter keys: {sync_filters.keys()}")
                if indexing_filters:
                    logger.debug(f"Indexing filter keys: {indexing_filters.keys()}")

        return sync_filters, indexing_filters

    except Exception as e:
        if logger:
            logger.error(f"Failed to load filters for {connector_name}: {e}")
        return empty_filters
