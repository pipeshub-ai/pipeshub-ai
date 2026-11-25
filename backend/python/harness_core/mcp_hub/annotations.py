"""An MCP tool's effect and sink from the protocol's own tool annotations (FG2, lead 2026-10-10).

The MCP spec lets a server describe a tool with behaviour hints: ``readOnlyHint`` (it changes nothing) and
``openWorldHint`` (it reaches an open world of external entities, ``true`` when absent). They decide approvals here:
a read-only tool is a read, and only a tool that declares ``openWorldHint: false`` keeps its arguments in the user's
own space (``self``). Every other tool, a read that declares nothing about its world included, sends what the model
chose to the server's operator (``external``), so the flow guard holds it after a private read (SEC-B-02, ADR-OWD-09
amendment 2026-11-13). A tool with no hints stays a send (fail closed, REPORT 6.5).

A hint is the server's claim about itself, so it is trusted only as far as the admin who installed the server: the
hints are part of the tool's descriptor digest (``ToolDescriptor``), so a server that flips one loses its approval and
the run's pin, and an org's approval rule for the tool or its class still applies over whatever the hint says.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.tools.base import Tag
from harness_core.tools.tags import EFFECT_TAG_KEY, TAG_SINK_EXTERNAL, TAG_SINK_SELF

if TYPE_CHECKING:
    from collections.abc import Mapping

HINT_KEYS: Final = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def declared_hints(annotations: object) -> dict[str, bool]:
    """The boolean behaviour hints a tool declares (a mapping or the SDK's model); anything else is left out."""
    if annotations is None:
        return {}
    if isinstance(annotations, dict):
        raw = annotations
    elif callable(dump := getattr(annotations, "model_dump", None)):
        # The SDK's model (snake_case fields): its protocol keys are the aliases.
        raw = dump(by_alias=True, exclude_none=True)
    else:
        raw = getattr(annotations, "__dict__", {})
    hints: dict[str, bool] = {}
    for key in HINT_KEYS:
        value = raw.get(key)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(value, bool):
            hints[key] = value
    return hints


def hint_tags(hints: Mapping[str, bool], *, inside_organization: bool = False) -> tuple[Tag, ...]:
    """The ``effect`` and ``sink`` tags the hints mean, the one mapping every MCP path uses; a tool with none is a
    write that may leave the deployment. The sink is always tagged: left untagged, a read falls to the
    first-party default (``self``, ``classify_tool_effect``), which is the hole SEC-B-02 was.

    ``inside_organization`` is the org admin's word that the server runs inside the organization (an instance
    setting, SEC-B-02-FU-1): its arguments stay inside, so the sink is ``self`` whatever the server claims. It never
    changes the effect: a write is still a write."""
    read = hints.get("readOnlyHint") is True
    closed_world = inside_organization or hints.get("openWorldHint") is False
    return (
        Tag(EFFECT_TAG_KEY, "read" if read else "write"),
        TAG_SINK_SELF if closed_world else TAG_SINK_EXTERNAL,
    )


__all__ = ["HINT_KEYS", "declared_hints", "hint_tags"]
