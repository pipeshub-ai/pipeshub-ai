"""A run's pinned catalog items, as effective-set items (02-lld §2.8), read from the pin it was admitted with.

Which pin is the authority is an explicit fact, never emptiness (CPD-10 R3): the pin's
``rollout.CONTROL_PLANE_CAPABILITIES`` row, true exactly when the capability migration recorded the run's org as
migrated. Then the effective set is the only authority (``offer``): the run holds the set's ready items and
nothing the set does not hold, whatever the spec's legacy fields still say. Without the row, or without a pin,
the items come from the spec's pins as before: each version-2 tool grant (CPS PN-5/PN-6) grouped by its
instance, and each pinned skill by its bundle. Only digests that bind something are items: an unversioned tool
pin predates descriptor pins and stays unverified, as it always was (NFR-42).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from app.control_plane.contracts.generated.effective_set import EffectiveItem
from app.harness.assembly.descriptor_pins import MCP_SOURCE
from harness_core.driver import CapabilityLost

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext

TOOLSET, MCP, SKILL = "toolset", "mcp", "skill"
# The version an item derived from a pin carries: the pin names no catalog version.
PINNED_VERSION = "pinned"
CAPABILITIES_ROW: Final = "rollout.CONTROL_PLANE_CAPABILITIES"
WEB_SOURCE: Final = "web"
# A toolset granted without an instance is the platform's own (Node pins it as ``<toolset>@default``): it is no
# control-plane item, so no effective set holds it and none takes it away.
PLATFORM_INSTANCE: Final = "default"


def skill_installation(name: str) -> str:
    """The same id the control plane's migration gives an agent's skill (``skillInstallationId``)."""
    return "skill_" + re.sub(r"[^A-Za-z0-9_-]+", "-", name)


def skill_tool_id(name: str) -> str:
    return f"{name}@{skill_installation(name)}/{name}"


def _item(kind: str, installation: str, name: str, tools: list[tuple[str, str]]) -> EffectiveItem:
    return EffectiveItem.model_validate(
        {
            "installation": installation,
            "item": f"{kind}:pipeshub/{name}@{PINNED_VERSION}",
            "kind": kind,
            "state": "ready",
            "reason": None,
            "tools": [{"toolId": tool_id, "descriptorDigest": digest} for tool_id, digest in sorted(tools)],
            "binding": None,
            "provenance": {"grantedBy": "org", "visibleVia": "org", "approvedBy": None},
        }
    )


def set_is_authority(run: RunContext) -> bool:
    """Whether the run's effective set, not its spec, says what it holds: its pin carries the row, true."""
    if run.resolved is None:
        return False
    entry = run.resolved.effective_config.dimensions.get(CAPABILITIES_ROW)
    return entry is not None and entry.value is True


def _parts(tool_id: str) -> tuple[str, str, str]:
    source, rest = tool_id.split("@", 1)
    instance, tool = rest.split("/", 1)
    return source, instance, tool


def _name(tool_id: str) -> str:
    return tool_id.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class Offer:
    """What a run whose effective set is the authority holds: the spec's grants the set holds ready."""

    tool_ids: tuple[str, ...]
    mcp_instances: tuple[str, ...]
    skills: tuple[str, ...]
    # The set's ready items the spec names, each with the tools the spec grants of it, at the set's digests.
    items: tuple[EffectiveItem, ...]

    def narrowed(self, run: RunContext) -> RunContext:
        """``run`` granted only this: every reader of its grants (the catalog, the tool environment, the
        credential resolution, the skills) then sees what the set holds and nothing else."""
        tools, servers, skills = set(self.tool_ids), set(self.mcp_instances), set(self.skills)
        whole = {
            pin.ref
            for pin in run.descriptor_pins
            if pin.kind == MCP and all(f"{MCP_SOURCE}@{pin.ref}/{name}" in tools for name in pin.tools)
        }
        return run.model_copy(
            update={
                "tool_ids": self.tool_ids,
                "mcp_instances": self.mcp_instances,
                "skills": self.skills,
                "skill_digests": {name: digest for name, digest in run.skill_digests.items() if name in skills},
                # A server pin covers the tools the spec granted: once the set holds fewer, its tools are
                # checked one by one at the set's digests instead.
                "descriptor_pins": tuple(
                    pin
                    for pin in run.descriptor_pins
                    if (pin.ref in tools if pin.kind == "tool" else pin.ref in servers and pin.ref in whole)
                ),
            }
        )


def offer(run: RunContext) -> Offer | None:
    """The run's grants as its effective set holds them; ``None`` when the spec's pins are the authority.

    A grant whose installation the set does not hold is not offered. One the set holds ``unavailable`` is
    ``CapabilityLost`` naming the set's reason: admission takes such an item out of the spec, so a spec that
    still grants it disagrees with its own pin, and the run never starts on less than it was told it has.
    Platform tools and web search are no control-plane items and pass through.
    """
    if not set_is_authority(run) or run.resolved is None:
        return None
    held = {item.installation.root: item for item in run.resolved.effective_set.items}
    lost: set[str] = set()

    def ready(installation: str) -> EffectiveItem | None:
        item = held.get(installation)
        if item is not None and item.state != "ready":
            lost.add(f"item:{installation}:{item.reason}")
            return None
        return item

    tool_ids: list[str] = []
    granted: dict[str, dict[str, str]] = {}
    for tool_id in run.tool_ids:
        source, instance, tool = _parts(tool_id)
        if source == WEB_SOURCE or instance == PLATFORM_INSTANCE:
            tool_ids.append(tool_id)
            continue
        item = ready(instance)
        if item is not None and any(_name(pinned.tool_id) == tool for pinned in item.tools):
            tool_ids.append(tool_id)
            granted.setdefault(instance, {})[tool] = tool_id
    skills = [name for name in run.skills if ready(skill_installation(name)) is not None]
    if lost:
        raise CapabilityLost(missing=sorted(lost))
    items = [_granted_item(held[installation], names) for installation, names in sorted(granted.items())]
    items += [held[skill_installation(name)] for name in sorted(skills)]
    return Offer(
        tool_ids=tuple(tool_ids),
        # A server granted whole (D-LAZY-2) names no tool the set could hold before its first listing.
        mcp_instances=tuple(i for i in run.mcp_instances if i in granted or i in run.mcp_at_first_use),
        skills=tuple(skills),
        items=tuple(items),
    )


def _granted_item(item: EffectiveItem, names: dict[str, str]) -> EffectiveItem:
    """The item with the tools the spec grants of it, under the ids the run's loader registers them by."""
    tools = [
        pinned.model_copy(update={"tool_id": names[_name(pinned.tool_id)]})
        for pinned in item.tools
        if _name(pinned.tool_id) in names
    ]
    return item.model_copy(update={"tools": tools})


def pinned_items(run: RunContext) -> tuple[EffectiveItem, ...]:
    offered = offer(run)
    if offered is not None:
        return offered.items
    by_instance: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for pin in run.descriptor_pins:
        if pin.kind != "tool":
            continue
        source, rest = pin.ref.split("@", 1)
        instance = rest.split("/", 1)[0]
        by_instance.setdefault((source, instance), []).append((pin.ref, pin.digest))
    items = [
        _item(
            MCP if source == MCP_SOURCE else TOOLSET,
            instance if source == MCP_SOURCE or instance != "default" else f"{source}-default",
            MCP_SOURCE if source == MCP_SOURCE else source,
            tools,
        )
        for (source, instance), tools in sorted(by_instance.items())
    ]
    items += [
        _item(SKILL, skill_installation(name), name, [(skill_tool_id(name), digest)])
        for name, digest in sorted(run.skill_digests.items())
    ]
    return tuple(items)


__all__ = [
    "CAPABILITIES_ROW",
    "MCP",
    "SKILL",
    "TOOLSET",
    "Offer",
    "offer",
    "pinned_items",
    "set_is_authority",
    "skill_installation",
    "skill_tool_id",
]
