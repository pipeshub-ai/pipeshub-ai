"""
Utility module for mapping connector-specific ticket values to standard enum values.

This module provides mapping functions that connectors can use to convert their
API-specific status and priority values to the standard TicketStatus and TicketPriority
enums. This ensures consistency across all ticketing connectors.
"""

from typing import Dict, Optional

from app.models.entities import TicketPriority, TicketStatus

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

    def __init__(
        self,
        status_mappings: Optional[Dict[str, TicketStatus]] = None,
        priority_mappings: Optional[Dict[str, TicketPriority]] = None,
    ) -> None:
        """
        Initialize the mapper with optional connector-specific mappings.

        Args:
            status_mappings: Connector-specific status mappings (merged with defaults)
            priority_mappings: Connector-specific priority mappings (merged with defaults)
        """
        self.status_mappings = {
            **self.DEFAULT_STATUS_MAPPINGS,
            **(status_mappings or {}),
        }
        self.priority_mappings = {
            **self.DEFAULT_PRIORITY_MAPPINGS,
            **(priority_mappings or {}),
        }

    def map_status(self, api_status: Optional[str]) -> Optional[TicketStatus]:
        """
        Map connector API status value to standard TicketStatus enum.

        Args:
            api_status: The status value from the connector API (e.g., "To Do", "In Progress")

        Returns:
            Standard TicketStatus enum value, or None if api_status is None/empty
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

        # If no match found, return UNKNOWN
        return TicketStatus.UNKNOWN

    def map_priority(self, api_priority: Optional[str]) -> Optional[TicketPriority]:
        """
        Map connector API priority value to standard TicketPriority enum.

        Args:
            api_priority: The priority value from the connector API (e.g., "High", "P1")

        Returns:
            Standard TicketPriority enum value, or None if api_priority is None/empty
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

        # If no match found, return UNKNOWN
        return TicketPriority.UNKNOWN


# Default mapper instance for convenience functions (performance optimization)
_default_mapper = TicketValueMapper()


# Convenience function for quick mapping without creating a mapper instance
def map_ticket_status(api_status: Optional[str], custom_mappings: Optional[Dict[str, TicketStatus]] = None) -> Optional[TicketStatus]:
    """Quick function to map status without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(status_mappings=custom_mappings)
        return mapper.map_status(api_status)
    return _default_mapper.map_status(api_status)


def map_ticket_priority(api_priority: Optional[str], custom_mappings: Optional[Dict[str, TicketPriority]] = None) -> Optional[TicketPriority]:
    """Quick function to map priority without creating a mapper instance"""
    if custom_mappings:
        mapper = TicketValueMapper(priority_mappings=custom_mappings)
        return mapper.map_priority(api_priority)
    return _default_mapper.map_priority(api_priority)
