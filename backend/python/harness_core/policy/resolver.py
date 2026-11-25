"""The one policy resolver (R2 CP3, 02-lld §5.1, 03-interfaces §4.1).

Layers fold top to bottom through the dimension registry. A ceiling only narrows: a widening is refused naming the
dimension and the first layer that asks for it, or kept at the stricter value where the row says ``meet``. A choice
is the nearest layer's, within its ceiling; an operator row is the deployment's. A layer's ``locks`` fix those
dimensions for every layer below it (C4.02): a different choice is refused as ``locked``, and so is a widening of a
locked ceiling, while narrowing one stays allowed. Node's
``domain/policy/resolver.ts`` is the twin; ``contracts/harness/vectors/policy-resolve.json`` holds the two to one
answer, digest included.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from harness_core.contracts import canonical_digest
from harness_core.contracts.generated.enums import LAYERS
from harness_core.policy.orders import UNDEFINED, InvalidPolicyValue, Widened
from harness_core.policy.registry import Dimension, policy_registry
from harness_core.policy.types import (
    EMPTY_PREFIX,
    DimensionEntry,
    FoldedPrefix,
    PolicyRefusal,
    Resolved,
    ScopedLayer,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.contracts.generated.enums import LayerName
    from harness_core.policy.registry import DimensionRegistry
    from harness_core.policy.types import PolicyRefusalCode

_ABSENT = object()
# The layer running work is narrowed by (02-lld §3.1): folded with ``live_fold``, never through ``resolve``.
LIVE = "live"


def _read(values: object, path: str) -> object:
    node = values
    for key in path.split("."):
        if not isinstance(node, Mapping) or key not in node:
            return _ABSENT
        node = cast("Mapping[str, object]", node)[key]
    return node


def scoped(layer: Mapping[str, JsonValue]) -> ScopedLayer:
    """A whole ``PolicyLayer`` (wire form) as the resolver reads it."""
    return ScopedLayer(cast("str", cast("Mapping[str, JsonValue]", layer["scope"])["kind"]), layer)


class DimensionPolicyResolver:
    """The generic resolver over a dimension registry (the release's table by default)."""

    def __init__(self, registry: DimensionRegistry | None = None) -> None:
        self._registry = registry if registry is not None else policy_registry()

    def resolve(self, layers: Sequence[ScopedLayer]) -> Resolved:
        return self.resolve_from(EMPTY_PREFIX, layers)

    def fold_prefix(self, layers: Sequence[ScopedLayer]) -> FoldedPrefix | PolicyRefusal:
        return self._fold(EMPTY_PREFIX, layers)

    def resolve_from(self, prefix: FoldedPrefix, rest: Sequence[ScopedLayer]) -> Resolved:
        folded = self._fold(prefix, rest)
        if isinstance(folded, PolicyRefusal):
            return Resolved(refusal=folded)
        refusal = self._outside_ceiling(folded)
        if refusal is not None:
            return Resolved(refusal=refusal)
        dimensions: dict[str, JsonValue] = {}
        for dimension in self._registry.all():
            entry = folded.entries.get(dimension.path)
            if entry is None or entry.value is UNDEFINED:
                continue
            effective: dict[str, JsonValue] = {
                "value": cast("JsonValue", copy.deepcopy(entry.value)),
                "setBy": entry.set_by,
                "ceilings": [{"layer": layer, "value": cast("JsonValue", copy.deepcopy(value))} for layer, value in entry.ceilings],
            }
            if dimension.path in folded.locks:
                effective["lockedBy"] = folded.locks[dimension.path]
            dimensions[dimension.path] = effective
        config: dict[str, JsonValue] = {"schemaVersion": 1, "dimensions": dimensions}
        return Resolved(config=config, digest=canonical_digest(config))

    def assert_narrows(self, layer: ScopedLayer, above: Sequence[ScopedLayer]) -> PolicyRefusal | None:
        """Whether ``layer`` only narrows what ``above`` resolves to; the refusal it would get otherwise."""
        return self.resolve([*above, layer]).refusal

    def live_fold(self, layers: Sequence[object], start: FoldedPrefix = EMPTY_PREFIX) -> FoldedPrefix:
        """Live layers as one, onto ``start``: every ceiling a ``live`` layer may set meets what is held, so a
        value looser than it changes nothing and nothing is refused. A choice or an operator row in a live layer
        is not the live layer's to set and is ignored. Raises ``InvalidPolicyValue`` for a value its order
        cannot read."""
        entries = dict(start.entries)
        for values in layers:
            for dimension in self._live_ceilings():
                raw = _read(values, dimension.layer_path)
                if raw is _ABSENT:
                    continue
                order = dimension.order
                assert order is not None
                held = entries.get(dimension.path)
                value = order.lift(raw)
                # A held UNDEFINED is the empty meet (two unrelated values): the bottom, which nothing lifts (CPD6-4).
                if held is None:
                    met = order.narrow(value, None)
                elif held.value is UNDEFINED:
                    met = UNDEFINED
                else:
                    met = order.meet(value, held.value)
                ceilings = (*(held.ceilings if held is not None else ()), (LIVE, raw))
                entries[dimension.path] = DimensionEntry(met, LIVE, ceilings)
        return FoldedPrefix(entries, start.locks)

    def narrowed_by_live(self, config: Mapping[str, JsonValue], live: FoldedPrefix) -> dict[str, JsonValue]:
        """``pin ⊓ live``: ``config`` (an ``EffectiveRunConfig``) with every ceiling the live layer holds met
        into it. Each ceiling of the result is at or below the pin's; a dimension the meet leaves without a
        value is absent, and what reads it refuses."""
        pinned = cast("Mapping[str, Mapping[str, JsonValue]]", config["dimensions"])
        # Entries the live layer does not touch are the pin's own, shared and never mutated (attempt-start cost).
        dimensions: dict[str, JsonValue] = cast("dict[str, JsonValue]", dict(pinned))
        for dimension in self._live_ceilings():
            held = live.entries.get(dimension.path)
            if held is None:
                continue
            order = dimension.order
            assert order is not None
            entry = pinned.get(dimension.path)
            met = held.value if entry is None or held.value is UNDEFINED else order.meet(held.value, entry["value"])
            if met is UNDEFINED:
                dimensions.pop(dimension.path, None)
                continue
            narrowed = entry is None or canonical_digest(met) != canonical_digest(entry["value"])
            effective: dict[str, JsonValue] = {
                "value": cast("JsonValue", copy.deepcopy(met)),
                "setBy": LIVE if narrowed else (entry["setBy"] if entry is not None else LIVE),
                "ceilings": [
                    *cast("list[JsonValue]", copy.deepcopy(entry["ceilings"]) if entry is not None else []),
                    *({"layer": LIVE, "value": cast("JsonValue", copy.deepcopy(raw))} for _, raw in held.ceilings),
                ],
            }
            if entry is not None and "lockedBy" in entry:
                effective["lockedBy"] = entry["lockedBy"]
            dimensions[dimension.path] = effective
        return {"schemaVersion": 1, "dimensions": dimensions}

    def _live_ceilings(self) -> list[Dimension]:
        return [
            dimension
            for dimension in self._registry.all()
            if dimension.order is not None and dimension.order.kind != "admits" and LIVE in dimension.exposure
        ]

    def _fold(self, start: FoldedPrefix, layers: Sequence[ScopedLayer]) -> FoldedPrefix | PolicyRefusal:
        entries = dict(start.entries)
        locks = dict(start.locks)
        for layer in layers:
            refusal = self._apply(entries, locks, layer)
            if refusal is not None:
                return refusal
        return FoldedPrefix(entries, locks)

    def _apply(self, entries: dict[str, DimensionEntry], locks: dict[str, str], layer: ScopedLayer) -> PolicyRefusal | None:
        refusal = self._apply_values(entries, locks, layer)
        return refusal if refusal is not None else self._apply_locks(locks, layer)

    def _apply_values(self, entries: dict[str, DimensionEntry], locks: Mapping[str, str], layer: ScopedLayer) -> PolicyRefusal | None:
        release = layer.kind == "release"
        for dimension in self._registry.all():
            raw = _read(layer.values, dimension.path if release else dimension.layer_path)
            if raw is _ABSENT:
                continue

            def refuse(code: PolicyRefusalCode, detail: str, at: str | None = None, d: Dimension = dimension) -> PolicyRefusal:
                return PolicyRefusal(code, d.path if at is None else f"{d.path}.{at}", layer.kind, detail)

            if not release and layer.kind not in dimension.exposure:
                return refuse("not_exposed", f"the {layer.kind} layer may not set it")
            current = entries.get(dimension.path)
            lock = locks.get(dimension.path)
            locked = lock is not None and not _carries(layer, dimension.path, lock)
            if dimension.order is None:
                if locked:
                    held = UNDEFINED if current is None else current.value
                    if held is UNDEFINED or canonical_digest(raw) != canonical_digest(held):
                        return refuse("locked", f"locked by the {lock} layer")
                    continue
                entries[dimension.path] = DimensionEntry(raw, layer.kind)
                continue
            ceilings = (*(current.ceilings if current is not None else ()), (layer.kind, raw))
            try:
                value = self._top(dimension, raw) if release else self._narrowed(dimension, raw, current)
            except InvalidPolicyValue as invalid:
                return refuse("invalid", str(invalid))
            if isinstance(value, Widened):
                if locked:
                    return refuse("locked", f"locked by the {lock} layer: {value.detail}", value.at)
                return refuse("widening", value.detail, value.at)
            keeps_chooser = dimension.order.kind == "admits" and current is not None
            entries[dimension.path] = DimensionEntry(value, current.set_by if keeps_chooser and current else layer.kind, ceilings)
        return None

    def _apply_locks(self, locks: dict[str, str], layer: ScopedLayer) -> PolicyRefusal | None:
        """Records the dimensions ``layer`` locks; the topmost lock of a dimension is the one that holds."""
        raw = _read(layer.values, "locks")
        if raw is _ABSENT:
            return None
        if not isinstance(raw, list):
            return PolicyRefusal("invalid", "locks", layer.kind, "locks is not a list of dimensions")
        for path in cast("list[object]", raw):
            dimension = self._registry.find(path) if isinstance(path, str) else None
            if dimension is None:
                return PolicyRefusal("invalid", str(path), layer.kind, "a lock names no dimension")
            if layer.kind != "release" and layer.kind not in dimension.exposure:
                return PolicyRefusal("not_exposed", dimension.path, layer.kind, f"the {layer.kind} layer may not lock it")
            locks.setdefault(dimension.path, layer.kind)
        return None

    @staticmethod
    def _top(dimension: Dimension, raw: object) -> object:
        """The release's value: the top every layer below narrows (an admitted item as it is)."""
        order = dimension.order
        assert order is not None
        return raw if order.kind == "admits" else order.lift(raw)

    @staticmethod
    def _narrowed(dimension: Dimension, raw: object, current: DimensionEntry | None) -> object:
        order = dimension.order
        assert order is not None
        value = order.lift(raw)
        if current is None or current.value is UNDEFINED:
            return UNDEFINED if order.kind == "admits" else order.narrow(value, None)
        if dimension.widening == "meet":
            return order.meet(value, current.value)
        widened = order.leq(value, current.value)
        return widened if widened is not None else order.narrow(value, current.value)

    def _outside_ceiling(self, prefix: FoldedPrefix) -> PolicyRefusal | None:
        for dimension in self._registry.all():
            if dimension.within is None:
                continue
            choice = prefix.entries.get(dimension.path)
            ceiling = prefix.entries.get(dimension.within)
            if choice is None or ceiling is None or ceiling.value is UNDEFINED:
                continue
            order = self._registry.get(dimension.within).order
            assert order is not None
            if not order.contains(ceiling.value, choice.value):
                return PolicyRefusal("outside_ceiling", dimension.path, choice.set_by, f"not within {dimension.within}")
        return None


def _carries(layer: ScopedLayer, path: str, lock: str) -> bool:
    """Whether ``layer`` holds the lock's authority for ``path``: its ``lockedBy`` gives the dimension's section to
    the locking layer or one above it (an org's rules written into an agent layer, 02-lld §11.1)."""
    owner = _read(layer.values, f"lockedBy.{path.split('.', 1)[0]}")
    return isinstance(owner, str) and owner in LAYERS and LAYERS.index(owner) <= LAYERS.index(cast("LayerName", lock))


__all__ = ["LIVE", "DimensionPolicyResolver", "scoped"]
