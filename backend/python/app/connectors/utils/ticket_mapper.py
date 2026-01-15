"""
Utility module for mapping connector-specific ticket values to standard enum values.

This module provides mapping functions that connectors can use to convert their
API-specific status and priority values to the standard TicketStatus and TicketPriority
enums. This ensures consistency across all ticketing connectors.
"""

import logging
from typing import Callable, Dict, Optional, TypeVar, Union

from app.config.constants.arangodb import LinkRelationshipTag
from app.models.entities import (
    TicketDeliveryStatus,
    TicketPriority,
    TicketStatus,
    TicketType,
)

logger = logging.getLogger(__name__)

# TypeVar for generic mapping value types
_T = TypeVar("_T")

# Priority level constants for numeric priority mapping
PRIORITY_HIGHEST = 1
PRIORITY_HIGH = 2
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 4
PRIORITY_LOWEST_THRESHOLD = 5

# Default mappings for common relationship tags (module-level constant for performance)
DEFAULT_TAG_MAPPINGS: Dict[str, LinkRelationshipTag] = {
    "relates to": LinkRelationshipTag.RELATES_TO,
    "relates_to": LinkRelationshipTag.RELATES_TO,
    "relatesto": LinkRelationshipTag.RELATES_TO,
    "blocks": LinkRelationshipTag.BLOCKS,
    "blocked by": LinkRelationshipTag.BLOCKED_BY,
    "blocked_by": LinkRelationshipTag.BLOCKED_BY,
    "blockedby": LinkRelationshipTag.BLOCKED_BY,
    "duplicates": LinkRelationshipTag.DUPLICATES,
    "duplicated by": LinkRelationshipTag.DUPLICATED_BY,
    "duplicated_by": LinkRelationshipTag.DUPLICATED_BY,
    "duplicatedby": LinkRelationshipTag.DUPLICATED_BY,
    "depends on": LinkRelationshipTag.DEPENDS_ON,
    "depends_on": LinkRelationshipTag.DEPENDS_ON,
    "dependson": LinkRelationshipTag.DEPENDS_ON,
    "required by": LinkRelationshipTag.REQUIRED_BY,
    "required_by": LinkRelationshipTag.REQUIRED_BY,
    "requiredby": LinkRelationshipTag.REQUIRED_BY,
    "clones": LinkRelationshipTag.CLONES,
    "cloned from": LinkRelationshipTag.CLONED_FROM,
    "cloned_from": LinkRelationshipTag.CLONED_FROM,
    "clonedfrom": LinkRelationshipTag.CLONED_FROM,
    "parent": LinkRelationshipTag.PARENT,
    "child": LinkRelationshipTag.CHILD,
    "related": LinkRelationshipTag.RELATED,
    "split from": LinkRelationshipTag.SPLIT_FROM,
    "split_from": LinkRelationshipTag.SPLIT_FROM,
    "splitfrom": LinkRelationshipTag.SPLIT_FROM,
    "merged into": LinkRelationshipTag.MERGED_INTO,
    "merged_into": LinkRelationshipTag.MERGED_INTO,
    "mergedinto": LinkRelationshipTag.MERGED_INTO,
}

# Priority level constants for numeric priority mapping
PRIORITY_HIGHEST = 1
PRIORITY_HIGH = 2
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 4
PRIORITY_LOWEST_THRESHOLD = 5


class TicketValueMapper:
    """Maps connector-specific ticket values to standard enum values"""

    @staticmethod
    def _normalize_value(value: str) -> str:
        """Normalize input value: lowercase and strip whitespace"""
        return value.lower().strip()

    @staticmethod
    def _find_partial_match(
        normalized: str,
        mappings: Dict[str, _T],
        match_func: Optional[Callable[[str, str], bool]] = None
    ) -> Optional[_T]:
        """
        Try to find a partial match in mappings.

        Args:
            normalized: Normalized input value
            mappings: Dictionary of mappings to search
            match_func: Optional custom match function (key, normalized) -> bool

        Returns:
            Mapped value if found, None otherwise
        """
        if match_func:
            for key, value in mappings.items():
                if match_func(key, normalized):
                    return value
        else:
            # Default: try underscore/space variations
            for key, value in mappings.items():
                if key.replace("_", " ") == normalized or normalized.replace(" ", "_") == key:
                    return value
        return None

    # Default status mappings (connector-specific mappings can override)
    DEFAULT_STATUS_MAPPINGS: Dict[str, TicketStatus] = {
        # Common status values
        "new": TicketStatus.NEW,
        "open": TicketStatus.OPEN,
        "in progress": TicketStatus.IN_PROGRESS,
        "in_progress": TicketStatus.IN_PROGRESS,
        "resolved": TicketStatus.RESOLVED,
        "closed": TicketStatus.CLOSED,
        "cancelled": TicketStatus.CANCELLED,
        "canceled": TicketStatus.CANCELLED,
        "reopened": TicketStatus.REOPENED,
        "pending": TicketStatus.PENDING,
        "waiting": TicketStatus.WAITING,
        "blocked": TicketStatus.BLOCKED,
        "done": TicketStatus.DONE,
        "completed": TicketStatus.DONE,
        "to do": TicketStatus.NEW,
        "todo": TicketStatus.NEW,
        "in review": TicketStatus.IN_PROGRESS,
        "in_review": TicketStatus.IN_PROGRESS,
        # Linear and other common workflow states
        "backlog": TicketStatus.NEW,
        "unstarted": TicketStatus.NEW,
        "started": TicketStatus.IN_PROGRESS,
        "planned": TicketStatus.NEW,
        "testing": TicketStatus.IN_PROGRESS,
    }

    # Default priority mappings (connector-specific mappings can override)
    DEFAULT_PRIORITY_MAPPINGS: Dict[str, TicketPriority] = {
        "lowest": TicketPriority.LOWEST,
        "low": TicketPriority.LOW,
        "medium": TicketPriority.MEDIUM,
        "normal": TicketPriority.MEDIUM,
        "high": TicketPriority.HIGH,
        "highest": TicketPriority.HIGHEST,
        "critical": TicketPriority.CRITICAL,
        "blocker": TicketPriority.BLOCKER,
        "urgent": TicketPriority.HIGHEST,  # Urgent typically means highest priority
        "trivial": TicketPriority.LOWEST,
        "minor": TicketPriority.LOW,
        "major": TicketPriority.HIGH,
        # No priority / None values
        "none": TicketPriority.UNKNOWN,
        "no priority": TicketPriority.UNKNOWN,
    }

    # Default type mappings (connector-specific mappings can override)
    DEFAULT_TYPE_MAPPINGS: Dict[str, TicketType] = {
        "task": TicketType.TASK,
        "tasks": TicketType.TASK,
        "bug": TicketType.BUG,
        "bugs": TicketType.BUG,
        "defect": TicketType.BUG,
        "story": TicketType.STORY,
        "stories": TicketType.STORY,
        "user story": TicketType.STORY,
        "epic": TicketType.EPIC,
        "epics": TicketType.EPIC,
        "feature": TicketType.FEATURE,
        "features": TicketType.FEATURE,
        "subtask": TicketType.SUBTASK,
        "sub-task": TicketType.SUBTASK,
        "incident": TicketType.INCIDENT,
        "incidents": TicketType.INCIDENT,
        "improvement": TicketType.IMPROVEMENT,
        "improvements": TicketType.IMPROVEMENT,
        "enhancement": TicketType.IMPROVEMENT,
        "enhancements": TicketType.IMPROVEMENT,
        "question": TicketType.QUESTION,
        "questions": TicketType.QUESTION,
        "documentation": TicketType.DOCUMENTATION,
        "docs": TicketType.DOCUMENTATION,
        "doc": TicketType.DOCUMENTATION,
        "test": TicketType.TEST,
        "tests": TicketType.TEST,
        "testing": TicketType.TEST,
        "test case": TicketType.TEST,
        "testcase": TicketType.TEST,
    }

    # Default delivery status mappings (connector-specific mappings can override)
    DEFAULT_DELIVERY_STATUS_MAPPINGS: Dict[str, TicketDeliveryStatus] = {
        "on track": TicketDeliveryStatus.ON_TRACK,
        "on_track": TicketDeliveryStatus.ON_TRACK,
        "ontrack": TicketDeliveryStatus.ON_TRACK,
        "at risk": TicketDeliveryStatus.AT_RISK,
        "at_risk": TicketDeliveryStatus.AT_RISK,
        "atrisk": TicketDeliveryStatus.AT_RISK,
        "off track": TicketDeliveryStatus.OFF_TRACK,
        "off_track": TicketDeliveryStatus.OFF_TRACK,
        "offtrack": TicketDeliveryStatus.OFF_TRACK,
        "high risk": TicketDeliveryStatus.HIGH_RISK,
        "high_risk": TicketDeliveryStatus.HIGH_RISK,
        "highrisk": TicketDeliveryStatus.HIGH_RISK,
        "some risk": TicketDeliveryStatus.SOME_RISK,
        "some_risk": TicketDeliveryStatus.SOME_RISK,
        "somerisk": TicketDeliveryStatus.SOME_RISK,
    }

    def __init__(
        self,
        status_mappings: Optional[Dict[str, TicketStatus]] = None,
        priority_mappings: Optional[Dict[str, TicketPriority]] = None,
        type_mappings: Optional[Dict[str, TicketType]] = None,
        delivery_status_mappings: Optional[Dict[str, TicketDeliveryStatus]] = None,
    ) -> None:
        """
        Initialize the mapper with optional connector-specific mappings.

        Args:
            status_mappings: Connector-specific status mappings (merged with defaults)
            priority_mappings: Connector-specific priority mappings (merged with defaults)
            type_mappings: Connector-specific type mappings (merged with defaults)
            delivery_status_mappings: Connector-specific delivery status mappings (merged with defaults)
        """
        self.status_mappings = {
            **self.DEFAULT_STATUS_MAPPINGS,
            **(status_mappings or {}),
        }
        self.priority_mappings = {
            **self.DEFAULT_PRIORITY_MAPPINGS,
            **(priority_mappings or {}),
        }
        self.type_mappings = {
            **self.DEFAULT_TYPE_MAPPINGS,
            **(type_mappings or {}),
        }
        self.delivery_status_mappings = {
            **self.DEFAULT_DELIVERY_STATUS_MAPPINGS,
            **(delivery_status_mappings or {}),
        }

    def map_status(self, api_status: Optional[str]) -> Optional[Union[TicketStatus, str]]:
        """
        Map connector API status value to standard TicketStatus enum.

        Args:
            api_status: The status value from the connector API (e.g., "To Do", "In Progress")

        Returns:
            Standard TicketStatus enum value, original string if no match found, or None if api_status is None/empty
        """
        if not api_status:
            return None

        normalized = self._normalize_value(api_status)

        # Try exact match first
        if normalized in self.status_mappings:
            return self.status_mappings[normalized]

        # Try to find partial match (e.g., "in progress" matches "in_progress")
        partial_match = self._find_partial_match(normalized, self.status_mappings)
        if partial_match:
            return partial_match

        # If no match found, log and return original value to preserve connector-specific status
        logger.debug(f"No mapping found for status '{api_status}', preserving original value")
        return api_status

    def map_priority(self, api_priority: Optional[str]) -> Optional[Union[TicketPriority, str]]:
        """
        Map connector API priority value to standard TicketPriority enum.

        Args:
            api_priority: The priority value from the connector API (e.g., "High", "P1")

        Returns:
            Standard TicketPriority enum value, original string if no match found, or None if api_priority is None/empty
        """
        if not api_priority:
            return None

        normalized = self._normalize_value(api_priority)

        # Try exact match first
        if normalized in self.priority_mappings:
            return self.priority_mappings[normalized]

        # Try numeric priority (e.g., "P1", "1" -> HIGHEST, "P5", "5" -> LOWEST)
        num_part = normalized[1:] if normalized.startswith("p") and len(normalized) > 1 else normalized

        if num_part.isdigit():
            try:
                num = int(num_part)
                if num == PRIORITY_HIGHEST:
                    return TicketPriority.HIGHEST
                elif num == PRIORITY_HIGH:
                    return TicketPriority.HIGH
                elif num == PRIORITY_MEDIUM:
                    return TicketPriority.MEDIUM
                elif num == PRIORITY_LOW:
                    return TicketPriority.LOW
                elif num >= PRIORITY_LOWEST_THRESHOLD:
                    return TicketPriority.LOWEST
            except ValueError:
                pass  # Should not happen due to isdigit() check

        # If no match found, log and return original value to preserve connector-specific priority
        logger.debug(f"No mapping found for priority '{api_priority}', preserving original value")
        return api_priority

    def map_type(self, api_type: Optional[str]) -> Optional[Union[TicketType, str]]:
        """
        Map connector API ticket type value to standard TicketType enum.

        Args:
            api_type: The ticket type value from the connector API (e.g., "Story", "Bug", "Epic")

        Returns:
            Standard TicketType enum value, original string if no match found, or None if api_type is None/empty
        """
        if not api_type:
            return None

        normalized = self._normalize_value(api_type)

        # Try exact match first
        if normalized in self.type_mappings:
            return self.type_mappings[normalized]

        # Try to find partial match (e.g., "user story" matches "story")
        partial_match = self._find_partial_match(
            normalized,
            self.type_mappings,
            match_func=lambda key, norm: key in norm or norm in key
        )
        if partial_match:
            return partial_match

        # If no match found, log and return original value to preserve connector-specific type
        logger.debug(f"No mapping found for type '{api_type}', preserving original value")
        return api_type

    def map_delivery_status(self, api_delivery_status: Optional[str]) -> Optional[Union[TicketDeliveryStatus, str]]:
        """
        Map connector API delivery status value to standard TicketDeliveryStatus enum.

        Args:
            api_delivery_status: The delivery status value from the connector API (e.g., "On Track", "At Risk", "Off Track")

        Returns:
            Standard TicketDeliveryStatus enum value, original string if no match found, or None if api_delivery_status is None/empty
        """
        if not api_delivery_status:
            return None

        normalized = self._normalize_value(api_delivery_status)

        # Try exact match first
        if normalized in self.delivery_status_mappings:
            return self.delivery_status_mappings[normalized]

        # Try to find partial match (e.g., "on track" matches "on_track")
        partial_match = self._find_partial_match(normalized, self.delivery_status_mappings)
        if partial_match:
            return partial_match

        # If no match found, log and return original value to preserve connector-specific delivery status
        logger.debug(f"No mapping found for delivery_status '{api_delivery_status}', preserving original value")
        return api_delivery_status


# Default mapper instance for convenience functions (performance optimization)
_default_mapper = TicketValueMapper()


# Convenience function for quick mapping without creating a mapper instance
def map_ticket_status(api_status: Optional[str], custom_mappings: Optional[Dict[str, TicketStatus]] = None) -> Optional[Union[TicketStatus, str]]:
    """Quick function to map status without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(status_mappings=custom_mappings)
        return mapper.map_status(api_status)
    return _default_mapper.map_status(api_status)


def map_ticket_priority(api_priority: Optional[str], custom_mappings: Optional[Dict[str, TicketPriority]] = None) -> Optional[Union[TicketPriority, str]]:
    """Quick function to map priority without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(priority_mappings=custom_mappings)
        return mapper.map_priority(api_priority)
    return _default_mapper.map_priority(api_priority)


def map_ticket_type(api_type: Optional[str], custom_mappings: Optional[Dict[str, TicketType]] = None) -> Optional[Union[TicketType, str]]:
    """Quick function to map ticket type without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(type_mappings=custom_mappings)
        return mapper.map_type(api_type)
    return _default_mapper.map_type(api_type)


def map_ticket_delivery_status(api_delivery_status: Optional[str], custom_mappings: Optional[Dict[str, TicketDeliveryStatus]] = None) -> Optional[Union[TicketDeliveryStatus, str]]:
    """Quick function to map delivery status without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(delivery_status_mappings=custom_mappings)
        return mapper.map_delivery_status(api_delivery_status)
    return _default_mapper.map_delivery_status(api_delivery_status)


def map_link_relationship_tag(api_tag: Optional[str], custom_mappings: Optional[Dict[str, LinkRelationshipTag]] = None) -> Optional[Union[LinkRelationshipTag, str]]:
    """
    Map connector API relationship tag value to standard LinkRelationshipTag enum.

    If no match is found, returns the original string value to preserve connector-specific tags.

    Args:
        api_tag: The relationship tag value from the connector API (e.g., "relates to", "blocks")
        custom_mappings: Optional connector-specific mappings to override defaults

    Returns:
        Standard LinkRelationshipTag enum value, original string if no match found, or None if api_tag is None/empty
    """
    if not api_tag:
        return None

    # Merge with custom mappings if provided
    tag_mappings = {**DEFAULT_TAG_MAPPINGS, **(custom_mappings or {})}

    # Normalize the input
    normalized = api_tag.lower().strip()

    # Try exact match first
    if normalized in tag_mappings:
        return tag_mappings[normalized]

    # Try to find partial match (e.g., "relates to" matches "relates_to")
    partial_match = TicketValueMapper._find_partial_match(normalized, tag_mappings)
    if partial_match:
        return partial_match

    # If no match found, return original value to preserve connector-specific tag
    return api_tag
