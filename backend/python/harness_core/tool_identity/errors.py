"""Typed refusals of instance-scoped tool identity; each ``reason_code`` names one cause (C-6)."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class ToolIdentityRefused(HarnessRefusal):
    reason_code: str = "tool_identity_refused"


class ToolRefInvalid(ToolIdentityRefused):
    """A tool id that is not ``source@instance/tool``."""

    reason_code = "tool_ref_invalid"


class ToolInstanceAmbiguous(ToolIdentityRefused):
    """A type-keyed reference that more than one instance of its type could satisfy: never guessed."""

    reason_code = "tool_instance_ambiguous"


class ToolInstanceMissing(ToolIdentityRefused):
    """A reference no bound instance satisfies."""

    reason_code = "tool_instance_missing"


__all__ = [
    "ToolIdentityRefused",
    "ToolInstanceAmbiguous",
    "ToolInstanceMissing",
    "ToolRefInvalid",
]
