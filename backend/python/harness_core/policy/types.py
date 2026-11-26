"""Policy resolution's value types (R2 CP3, 03-interfaces §4.1)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from harness_core.policy.orders import UNDEFINED

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

PolicyRefusalCode = Literal["widening", "not_exposed", "outside_ceiling", "invalid", "locked"]


@dataclass(frozen=True, slots=True)
class ScopedLayer:
    """One layer: where it sits in the chain (a ``LayerName``) and its values (a policy layer, or one section)."""

    kind: str
    values: object


@dataclass(frozen=True, slots=True)
class PolicyRefusal:
    code: PolicyRefusalCode
    dimension: str
    layer: str
    detail: str

    def wire(self) -> dict[str, JsonValue]:
        return {"code": self.code, "dimension": self.dimension, "layer": self.layer}


@dataclass(frozen=True, slots=True)
class DimensionEntry:
    value: object
    set_by: str
    ceilings: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class FoldedPrefix:
    """The fold of a chain's first layers, to resolve the rest onto (02-lld §8.2)."""

    entries: Mapping[str, DimensionEntry] = field(default_factory=dict[str, DimensionEntry])
    locks: Mapping[str, str] = field(default_factory=dict[str, str])
    """Each locked dimension and the layer that locked it (C4.02)."""

    def document(self) -> dict[str, object]:
        """The values as one document: each dimension's value at its path."""
        out: dict[str, object] = {}
        for path, entry in self.entries.items():
            if entry.value is UNDEFINED:
                continue
            *parents, last = path.split(".")
            node = out
            for key in parents:
                node = cast("dict[str, object]", node.setdefault(key, {}))
            node[last] = copy.deepcopy(entry.value)
        return out


EMPTY_PREFIX = FoldedPrefix()


@dataclass(frozen=True, slots=True)
class Resolved:
    """An ``EffectiveRunConfig`` (wire form) and its digest, or the refusal."""

    config: dict[str, JsonValue] | None = None
    digest: str | None = None
    refusal: PolicyRefusal | None = None


__all__ = ["EMPTY_PREFIX", "DimensionEntry", "FoldedPrefix", "PolicyRefusal", "PolicyRefusalCode", "Resolved", "ScopedLayer"]
