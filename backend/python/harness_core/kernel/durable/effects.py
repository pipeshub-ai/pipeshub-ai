"""Effect metadata for tool calls until P2 tool descriptors exist.

``classify_tool_effect`` is the one place that turns today's tool tags into an
effect class. Replace it (or pass another classifier to the tool port) once
descriptors carry ``effect``/``idempotent`` explicitly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from pydantic import JsonValue

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.risk import risk_mark
from harness_core.effects.types import UNKNOWN_TOOL, EffectKind, ToolEffect, tool_id_for
from harness_core.tools.tags import (
    EFFECT_TAG_KEY,
    FLOW_TAG_KEY,
    SINK_TAG_KEY,
    TAG_FLOW_OPENS_ADDRESS,
    TAG_FLOW_PLATFORM_WEB,
    TAG_PROVIDER_PLATFORM,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.tools.base import Tag

# A tool whose effect depends on its arguments (a model-written query) names
# the class of one call; the tool's static class is the fail-closed default.
CallEffect = Callable[[Mapping[str, JsonValue]], EffectKind]

_WRITE_TYPES = frozenset(
    {"write", "create", "update", "delete", "send", "post", "upload"}
)
_READ_TYPES = frozenset({"read", "search", "list", "get", "fetch", "query"})
_EFFECTS: frozenset[str] = frozenset({"read", "workspace", "write", "execute"})
_EGRESS: frozenset[str] = frozenset({"none", "self", "internal_audience", "external"})


def classify_tool_effect(name: str, tags: Iterable[Tag]) -> ToolEffect:
    """Fail closed: a tool is ``read`` only with positive evidence.

    * ``effect=<kind>`` (and ``idempotent=true``) set the class explicitly.
    * ``category=execute`` -> ``execute``.
    * ``category=write`` or a writing ``type`` (write/create/update/delete/...) -> ``write``.
    * ``category=read`` or a reading ``type`` (read/search/list/get/...) -> ``read``.
    * anything else -> ``write``, not idempotent.
    """
    values: dict[str, set[str]] = {}
    for tag in tags:
        values.setdefault(tag.key, set()).add(tag.value.lower())
    category = values.get("category", set())
    kind_values = values.get("type", set())
    explicit = values.get(EFFECT_TAG_KEY, set()) & _EFFECTS
    effect: EffectKind
    if explicit:
        effect = _strongest(explicit)
    elif "execute" in category:
        effect = "execute"
    elif "write" in category or kind_values & _WRITE_TYPES:
        effect = "write"
    elif "read" in category or kind_values & _READ_TYPES:
        effect = "read"
    else:
        effect = "write"
    idempotent = effect in ("read", "workspace") or "true" in values.get(
        "idempotent", set()
    )
    provider = next(iter(sorted(values.get("provider", set()))), "local")
    platform_web = (
        effect == "read"
        and values.get("provider") == {TAG_PROVIDER_PLATFORM.value}
        and TAG_FLOW_PLATFORM_WEB.value in values.get(FLOW_TAG_KEY, set())
    )
    return ToolEffect(
        tool_id=tool_id_for(name, source=provider),
        effect=effect,
        idempotent=idempotent,
        sink=_sink(effect, values.get(SINK_TAG_KEY, set())),
        mark=risk_mark(values),
        platform_web=platform_web,
        opens_address=platform_web and TAG_FLOW_OPENS_ADDRESS.value in values.get(FLOW_TAG_KEY, set()),
    )


def _sink(effect: EffectKind, declared: set[str]) -> SinkDescriptor:
    """A ``workspace`` tool's arguments stay in the run. Otherwise the tool's ``sink`` tag says where they go
    (the flow rule holds an ``external`` one after a private read); several or an unknown value is ``external``
    (fail closed), and so is none on a write. A read is a read only with positive evidence (its effect tag, reviewed
    per tool), and its request goes to the system it reads with the user's own access: ``self`` unless it declares
    that it sends what the model chose elsewhere (``sink=external``, FG2)."""
    if effect == "workspace":
        return SinkDescriptor(egress="none", args_leave_boundary=False)
    if not declared and effect == "read":
        return SinkDescriptor(egress="self", args_leave_boundary=False)
    egress = next(iter(declared)) if len(declared) == 1 else "external"
    if egress not in _EGRESS:
        egress = "external"
    return SinkDescriptor(egress=egress, args_leave_boundary=egress == "external")  # type: ignore[arg-type]


def _strongest(kinds: set[str]) -> EffectKind:
    for kind in ("execute", "write", "workspace", "read"):
        if kind in kinds:
            return kind
    return "write"


def with_call_effect(effect: ToolEffect, kind: EffectKind) -> ToolEffect:
    """``effect`` for one call of class ``kind``, with the idempotency and sink
    ``classify_tool_effect`` gives that class; a write keeps the tool's own
    idempotency only if the tool is a write."""
    if kind == effect.effect:
        return effect
    reads = ("read", "workspace")
    # A write tool's own sink describes its writes; a call raised from a read has none declared, and a read call of
    # a write tool reads the system the tool writes to.
    keeps_sink = kind not in reads and effect.effect not in reads
    return dataclasses.replace(
        effect,
        effect=kind,
        idempotent=kind in reads or (effect.effect not in reads and effect.idempotent),
        sink=effect.sink if keeps_sink else _sink(kind, set()),
        platform_web=effect.platform_web and kind == "read",
        opens_address=effect.opens_address and kind == "read",
    )


__all__ = [
    "UNKNOWN_TOOL",
    "CallEffect",
    "EffectKind",
    "ToolEffect",
    "classify_tool_effect",
    "tool_id_for",
    "with_call_effect",
]
