"""The provenance rule each PipesHub tool source declares its tools with (ADR-RUN-01 D3).

A ``SourcedTool`` cannot be built without a rule, and the tool port and the Tool Gateway refuse a served tool
without one (``ProvenanceStamp.for_tools``). Rules are declared per kind of source, never by an outside tool's own
word:

- every MCP server's tools: ``MCP_RULE`` (unknown, outsider-written, errors included);
- every connector's actions: ``CONNECTOR_RULE`` (the owner's items, first party as the flow guard read them; unknown unless what the call
  put in the citation state names them);
- the web tools: ``WEB_RULE`` (public, outsider-written);
- a delegate: ``DELEGATE_RULE`` (unknown; a call that spawned a child run is the child's to say);
- the platform's own tools: by what they return (``platform_rule``): records (their records ride the citation state),
  no record content, a result derived from the call's own arguments (``TAG_OUTPUT_DERIVED``), or, for a platform
  tool none of those names (code a sandbox ran, the workspace), ``OPAQUE_RULE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.assembly.tool_sources import PLATFORM
from app.harness.labels import derived_output, tool_kind
from harness_core.provenance.rules import (
    CONNECTOR_RULE,
    DERIVED_RULE,
    NO_CONTENT_RULE,
    OPAQUE_RULE,
    RECORDS_RULE,
    WEB_RULE,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.effects.types import ToolEffect
    from harness_core.provenance.rules import ProvenanceRule


def platform_rule(name: str, tool_id: str) -> ProvenanceRule:
    """A platform tool's rule, from the release's own tables of what each tool returns."""
    match tool_kind(name):
        case "records":
            return RECORDS_RULE
        case "none":
            return NO_CONTENT_RULE
        case "public":
            return WEB_RULE
        case _:
            return DERIVED_RULE if derived_output(tool_id) else OPAQUE_RULE


def connector_rule(name: str, tool_id: str) -> ProvenanceRule:
    """A connector toolset's tool: a platform-declared kind it shares (a knowledge read, a question) keeps that rule;
    every action is ``CONNECTOR_RULE``."""
    declared = platform_rule(name, tool_id)
    return CONNECTOR_RULE if declared in (OPAQUE_RULE, DERIVED_RULE) else declared


def added_rules(effects: Mapping[str, ToolEffect], declared: Mapping[str, ProvenanceRule]) -> dict[str, ProvenanceRule]:
    """``declared`` with a rule for each tool the host added to a grant after its catalog was built (an engine's record
    fetch, its pull-request tool): a platform tool by what it returns, any other by the connector rule."""
    rules = dict(declared)
    for name, effect in effects.items():
        if name not in rules:
            platform = effect.tool_id.startswith(f"{PLATFORM}@")
            rules[name] = platform_rule(name, effect.tool_id) if platform else connector_rule(name, effect.tool_id)
    return rules


__all__ = ["added_rules", "connector_rule", "platform_rule"]
