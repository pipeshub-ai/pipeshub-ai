"""
Utility module for mapping connector-specific ticket values to standard enum values.

This module provides mapping functions that connectors can use to convert their
API-specific status and priority values to the standard TicketStatus and TicketPriority
enums. This ensures consistency across all ticketing connectors.
"""

from typing import Dict, Optional, Union

from app.models.entities import (
    TicketDeliveryStatus,
    TicketPriority,
    TicketStatus,
    TicketType,
)

# Priority level constants for numeric priority mapping
PRIORITY_HIGHEST = 1
PRIORITY_HIGH = 2
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 4
PRIORITY_LOWEST_THRESHOLD = 5


class TicketValueMapper:
    """Maps connector-specific ticket values to standard enum values"""

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
    }

    # Default priority mappings (connector-specific mappings can override)
    DEFAULT_PRIORITY_MAPPINGS: Dict[str, TicketPriority] = {
        # Common priority values
        "lowest": TicketPriority.LOWEST,
        "low": TicketPriority.LOW,
        "medium": TicketPriority.MEDIUM,
        "normal": TicketPriority.MEDIUM,
        "high": TicketPriority.HIGH,
        "highest": TicketPriority.HIGHEST,
        "critical": TicketPriority.CRITICAL,
        "blocker": TicketPriority.BLOCKER,
        "urgent": TicketPriority.CRITICAL,
        "trivial": TicketPriority.LOWEST,
        "minor": TicketPriority.LOW,
        "major": TicketPriority.HIGH,
    }

    # Default type mappings (connector-specific mappings can override)
    DEFAULT_TYPE_MAPPINGS: Dict[str, TicketType] = {
        # Common ticket type values
        "task": TicketType.TASK,
        "bug": TicketType.BUG,
        "story": TicketType.STORY,
        "epic": TicketType.EPIC,
        "feature": TicketType.FEATURE,
        "subtask": TicketType.SUBTASK,
        "sub-task": TicketType.SUBTASK,
        "incident": TicketType.INCIDENT,
        "improvement": TicketType.IMPROVEMENT,
        "question": TicketType.QUESTION,
        "documentation": TicketType.DOCUMENTATION,
        "doc": TicketType.DOCUMENTATION,
        "test": TicketType.TEST,
        "test case": TicketType.TEST,
        "testcase": TicketType.TEST,
    }

    # Default delivery status mappings (connector-specific mappings can override)
    DEFAULT_DELIVERY_STATUS_MAPPINGS: Dict[str, TicketDeliveryStatus] = {
        # Common delivery status values
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

        # Normalize the input: lowercase and strip whitespace
        normalized = api_priority.lower().strip()

        # Try exact match first
        if normalized in self.priority_mappings:
            return self.priority_mappings[normalized]

        # Try numeric priority (e.g., "P1", "1" -> HIGHEST, "P5", "5" -> LOWEST)
        num_part = normalized
        if normalized.startswith("p") and len(normalized) > 1:
            num_part = normalized[1:]

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

        # If no match found, return original value to preserve connector-specific priority
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
