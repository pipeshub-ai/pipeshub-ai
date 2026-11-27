"""The ``schedules`` toolset (ADR-SCH-03 D8): one on-demand group for the schedule tools, on every chat path.

On demand: the model sees the group as one line and loads its tools through discovery, whatever the grant's size,
and the group never counts toward a lazy threshold. Preloading unlocks it before the first turn when the request
clearly asks for something later or repeated (``CUES``), and points at it on a weaker sign (``HINTS``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from harness_core.tools.registry import ToolRegistry

SCHEDULES_TOOLSET: Final = "schedules"
SCHEDULES_DESCRIPTION: Final = (
    "Set up, find, change, pause and delete reminders and recurring runs (\"remind me in 5 minutes\", \"every Monday\")"
)

_AMOUNT: Final = r"(?:\d+|an?|one|two|three|four|five|ten|fifteen|twenty|thirty|a\s+few|a\s+couple\s+of)"
_UNIT: Final = r"(?:secs?|seconds?|mins?|minutes?|hrs?|hours?|days?|weeks?)"
_DAY: Final = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)"
)
_PERIOD: Final = rf"(?:day|morning|afternoon|evening|night|week|weekday|weekend|month|year|hour|{_DAY})s?"

CUES: Final = (
    r"\bremind(?:er|ers)?\b",
    rf"\b(?:in|after)\s+{_AMOUNT}\s*{_UNIT}\b",
    rf"\b(?:every|each)\s+(?:{_PERIOD}|\d+\s*{_UNIT})\b",
    r"\b(?:recurring|on\s+a\s+schedule)\b",
)
# A weaker sign: "later", "tomorrow", "weekly", "at 9am" also come up in requests that want an answer now.
HINTS: Final = (
    r"\b(?:later|tomorrow|tonight|daily|weekly|monthly|hourly|schedules?|cron|periodically)\b",
    r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
)


def register_schedules_toolset(registry: ToolRegistry, tool_names: list[str]) -> None:
    """Groups ``tool_names`` (the schedule tools this run was offered) as the on-demand ``schedules`` toolset."""
    registry.register_toolset(
        SCHEDULES_TOOLSET, SCHEDULES_DESCRIPTION, tool_names, on_demand=True, cues=CUES, hints=HINTS,
    )


__all__ = ["CUES", "HINTS", "SCHEDULES_DESCRIPTION", "SCHEDULES_TOOLSET", "register_schedules_toolset"]
