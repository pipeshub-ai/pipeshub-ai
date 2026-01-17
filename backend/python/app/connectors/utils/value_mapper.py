"""
Utility module for mapping connector-specific values to standard enum values.

This module provides mapping functions that connectors can use to convert their
API-specific status and priority values to the standard Status and Priority
enums. This ensures consistency across all connectors.
"""

from typing import Dict, Optional, Union

from app.config.constants.arangodb import LinkRelationshipTag
from app.models.entities import (
    DeliveryStatus,
    ItemType,
    Priority,
    Status,
)

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
    "is blocked by": LinkRelationshipTag.BLOCKED_BY,
    "duplicates": LinkRelationshipTag.DUPLICATES,
    "duplicated by": LinkRelationshipTag.DUPLICATED_BY,
    "duplicated_by": LinkRelationshipTag.DUPLICATED_BY,
    "duplicatedby": LinkRelationshipTag.DUPLICATED_BY,
    "is duplicated by": LinkRelationshipTag.DUPLICATED_BY,
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
    "is cloned by": LinkRelationshipTag.CLONED_BY,
    "is_cloned_by": LinkRelationshipTag.CLONED_BY,
    "isclonedby": LinkRelationshipTag.CLONED_BY,
    "implements": LinkRelationshipTag.IMPLEMENTS,
    "is implemented by": LinkRelationshipTag.IMPLEMENTED_BY,
    "is_implemented_by": LinkRelationshipTag.IMPLEMENTED_BY,
    "isimplementedby": LinkRelationshipTag.IMPLEMENTED_BY,
    "reviews": LinkRelationshipTag.REVIEWS,
    "is reviewed by": LinkRelationshipTag.REVIEWED_BY,
    "is_reviewed_by": LinkRelationshipTag.REVIEWED_BY,
    "isreviewedby": LinkRelationshipTag.REVIEWED_BY,
    "causes": LinkRelationshipTag.CAUSES,
    "is caused by": LinkRelationshipTag.CAUSED_BY,
    "is_caused_by": LinkRelationshipTag.CAUSED_BY,
    "iscausedby": LinkRelationshipTag.CAUSED_BY,
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

class ValueMapper:
    """Maps connector-specific values to standard enum values"""

    # Default status mappings (connector-specific mappings can override)
    DEFAULT_STATUS_MAPPINGS: Dict[str, Status] = {
        # Common status values
        "new": Status.NEW,
        "open": Status.OPEN,
        "in progress": Status.IN_PROGRESS,
        "in_progress": Status.IN_PROGRESS,
        "resolved": Status.RESOLVED,
        "closed": Status.CLOSED,
        "cancelled": Status.CANCELLED,
        "canceled": Status.CANCELLED,
        "reopened": Status.REOPENED,
        "pending": Status.PENDING,
        "waiting": Status.WAITING,
        "blocked": Status.BLOCKED,
        "done": Status.DONE,
        "completed": Status.DONE,
        "to do": Status.NEW,
        "todo": Status.NEW,
        "in review": Status.QA,
        "in_review": Status.QA,
        "review": Status.QA,
        "testing": Status.QA,
        "qa": Status.QA,
        # Linear and other common workflow states
        "backlog": Status.NEW,
        "unstarted": Status.NEW,
        "started": Status.IN_PROGRESS,
        "planned": Status.NEW,
    }

    # Default priority mappings (connector-specific mappings can override)
    DEFAULT_PRIORITY_MAPPINGS: Dict[str, Priority] = {
        "lowest": Priority.LOWEST,
        "low": Priority.LOW,
        "medium": Priority.MEDIUM,
        "normal": Priority.MEDIUM,
        "high": Priority.HIGH,
        "highest": Priority.HIGHEST,
        "critical": Priority.CRITICAL,
        "blocker": Priority.BLOCKER,
        "urgent": Priority.HIGHEST,  # Urgent typically means highest priority
        "trivial": Priority.LOWEST,
        "minor": Priority.LOW,
        "major": Priority.HIGH,
        # No priority / None values
        "none": Priority.UNKNOWN,
        "no priority": Priority.UNKNOWN,
        "0": Priority.UNKNOWN,
        "p0": Priority.UNKNOWN,
    }

    # Default type mappings (connector-specific mappings can override)
    DEFAULT_TYPE_MAPPINGS: Dict[str, ItemType] = {
        "task": ItemType.TASK,
        "tasks": ItemType.TASK,
        "bug": ItemType.BUG,
        "bugs": ItemType.BUG,
        "defect": ItemType.BUG,
        "story": ItemType.STORY,
        "stories": ItemType.STORY,
        "user story": ItemType.STORY,
        "epic": ItemType.EPIC,
        "epics": ItemType.EPIC,
        "feature": ItemType.FEATURE,
        "features": ItemType.FEATURE,
        "subtask": ItemType.SUBTASK,
        "sub-task": ItemType.SUBTASK,
        "incident": ItemType.INCIDENT,
        "incidents": ItemType.INCIDENT,
        "improvement": ItemType.IMPROVEMENT,
        "improvements": ItemType.IMPROVEMENT,
        "enhancement": ItemType.IMPROVEMENT,
        "enhancements": ItemType.IMPROVEMENT,
        "question": ItemType.QUESTION,
        "questions": ItemType.QUESTION,
        "documentation": ItemType.DOCUMENTATION,
        "docs": ItemType.DOCUMENTATION,
        "doc": ItemType.DOCUMENTATION,
        "test": ItemType.TEST,
        "tests": ItemType.TEST,
        "testing": ItemType.TEST,
        "test case": ItemType.TEST,
        "testcase": ItemType.TEST,
    }

    # Default delivery status mappings (connector-specific mappings can override)
    DEFAULT_DELIVERY_STATUS_MAPPINGS: Dict[str, DeliveryStatus] = {
        "on track": DeliveryStatus.ON_TRACK,
        "on_track": DeliveryStatus.ON_TRACK,
        "ontrack": DeliveryStatus.ON_TRACK,
        "at risk": DeliveryStatus.AT_RISK,
        "at_risk": DeliveryStatus.AT_RISK,
        "atrisk": DeliveryStatus.AT_RISK,
        "off track": DeliveryStatus.OFF_TRACK,
        "off_track": DeliveryStatus.OFF_TRACK,
        "offtrack": DeliveryStatus.OFF_TRACK,
        "high risk": DeliveryStatus.HIGH_RISK,
        "high_risk": DeliveryStatus.HIGH_RISK,
        "highrisk": DeliveryStatus.HIGH_RISK,
        "some risk": DeliveryStatus.SOME_RISK,
        "some_risk": DeliveryStatus.SOME_RISK,
        "somerisk": DeliveryStatus.SOME_RISK,
    }

    def __init__(
        self,
        status_mappings: Optional[Dict[str, Status]] = None,
        priority_mappings: Optional[Dict[str, Priority]] = None,
        type_mappings: Optional[Dict[str, ItemType]] = None,
        delivery_status_mappings: Optional[Dict[str, DeliveryStatus]] = None,
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

    def map_status(self, api_status: Optional[str]) -> Optional[Union[Status, str]]:
        """
        Map connector API status value to standard Status enum.

        Args:
            api_status: The status value from the connector API (e.g., "To Do", "In Progress")

        Returns:
            Standard Status enum value, original string if no match found, or None if api_status is None/empty
        """
        if not api_status:
            return None

        # Normalize the input: lowercase and strip whitespace
        normalized = api_status.lower().strip()

        # Try exact match first
        if normalized in self.status_mappings:
            return self.status_mappings[normalized]

        # Try to find partial match (e.g., "in progress" matches "in_progress")
        for key, value in self.status_mappings.items():
            if key.replace("_", " ") == normalized or normalized.replace(" ", "_") == key:
                return value

        # If no match found, return original value to preserve connector-specific status
        return api_status

    def map_priority(self, api_priority: Optional[str]) -> Optional[Union[Priority, str]]:
        """
        Map connector API priority value to standard Priority enum.

        Args:
            api_priority: The priority value from the connector API (e.g., "High", "P1")

        Returns:
            Standard Priority enum value, original string if no match found, or None if api_priority is None/empty
        """
        if not api_priority:
            return None

        # Normalize the input: lowercase and strip whitespace
        normalized = api_priority.lower().strip()

        # Try exact match first
        if normalized in self.priority_mappings:
            return self.priority_mappings[normalized]

        # Try numeric priority (e.g., "P0", "0" -> UNKNOWN/No Priority, "P1", "1" -> HIGHEST, "P5", "5" -> LOWEST)
        num_part = normalized[1:] if normalized.startswith("p") and len(normalized) > 1 else normalized

        if num_part.isdigit():
            try:
                num = int(num_part)
                if num == 0:
                    # 0 or P0 = No Priority (used by Linear and some other systems)
                    return Priority.UNKNOWN
                elif num == PRIORITY_HIGHEST:
                    return Priority.HIGHEST
                elif num == PRIORITY_HIGH:
                    return Priority.HIGH
                elif num == PRIORITY_MEDIUM:
                    return Priority.MEDIUM
                elif num == PRIORITY_LOW:
                    return Priority.LOW
                elif num >= PRIORITY_LOWEST_THRESHOLD:
                    return Priority.LOWEST
            except ValueError:
                pass  # Should not happen due to isdigit() check

        # If no match found, return original value to preserve connector-specific priority
        return api_priority

    def map_type(self, api_type: Optional[str]) -> Optional[Union[ItemType, str]]:
        """
        Map connector API type value to standard ItemType enum.

        Args:
            api_type: The type value from the connector API (e.g., "Story", "Bug", "Epic")

        Returns:
            Standard ItemType enum value, original string if no match found, or None if api_type is None/empty
        """
        if not api_type:
            return None

        # Normalize the input: lowercase and strip whitespace
        normalized = api_type.lower().strip()

        # Try exact match first
        if normalized in self.type_mappings:
            return self.type_mappings[normalized]

        # Try to find partial match (e.g., "user story" matches "story")
        for key, value in self.type_mappings.items():
            if key in normalized or normalized in key:
                return value

        # If no match found, return original value to preserve connector-specific type
        return api_type

    def map_delivery_status(self, api_delivery_status: Optional[str]) -> Optional[Union[DeliveryStatus, str]]:
        """
        Map connector API delivery status value to standard DeliveryStatus enum.

        Args:
            api_delivery_status: The delivery status value from the connector API (e.g., "On Track", "At Risk", "Off Track")

        Returns:
            Standard DeliveryStatus enum value, original string if no match found, or None if api_delivery_status is None/empty
        """
        if not api_delivery_status:
            return None

        # Normalize the input: lowercase and strip whitespace
        normalized = api_delivery_status.lower().strip()

        # Try exact match first
        if normalized in self.delivery_status_mappings:
            return self.delivery_status_mappings[normalized]

        # Try to find partial match (e.g., "on track" matches "on_track")
        for key, value in self.delivery_status_mappings.items():
            if key.replace("_", " ") == normalized or normalized.replace(" ", "_") == key:
                return value

        # If no match found, return original value to preserve connector-specific delivery status
        return api_delivery_status


# Default mapper instance for convenience functions (performance optimization)
_default_mapper = ValueMapper()


# Convenience function for quick mapping without creating a mapper instance
def map_status(api_status: Optional[str], custom_mappings: Optional[Dict[str, Status]] = None) -> Optional[Union[Status, str]]:
    """Quick function to map status without creating a mapper instance"""
    if custom_mappings:
        mapper = ValueMapper(status_mappings=custom_mappings)
        return mapper.map_status(api_status)
    return _default_mapper.map_status(api_status)


def map_priority(api_priority: Optional[str], custom_mappings: Optional[Dict[str, Priority]] = None) -> Optional[Union[Priority, str]]:
    """Quick function to map priority without creating a mapper instance"""
    if custom_mappings:
        mapper = ValueMapper(priority_mappings=custom_mappings)
        return mapper.map_priority(api_priority)
    return _default_mapper.map_priority(api_priority)


def map_type(api_type: Optional[str], custom_mappings: Optional[Dict[str, ItemType]] = None) -> Optional[Union[ItemType, str]]:
    """Quick function to map type without creating a mapper instance"""
    if custom_mappings:
        mapper = ValueMapper(type_mappings=custom_mappings)
        return mapper.map_type(api_type)
    return _default_mapper.map_type(api_type)


def map_delivery_status(api_delivery_status: Optional[str], custom_mappings: Optional[Dict[str, DeliveryStatus]] = None) -> Optional[Union[DeliveryStatus, str]]:
    """Quick function to map delivery status without creating a mapper instance"""
    if custom_mappings:
        mapper = ValueMapper(delivery_status_mappings=custom_mappings)
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

    # Normalize the input: lowercase and strip whitespace
    normalized = api_tag.lower().strip()

    # Try exact match first
    if normalized in tag_mappings:
        return tag_mappings[normalized]

    # Try to find partial match (e.g., "relates to" matches "relates_to")
    partial_match = ValueMapper._find_partial_match(normalized, tag_mappings)
    if partial_match:
        return partial_match

    # If no match found, return original value to preserve connector-specific tag
    return api_tag
