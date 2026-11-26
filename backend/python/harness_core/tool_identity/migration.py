"""Planning the move of saved definitions from type-keyed to instance-keyed toolsets (pure).

A type-keyed binding (a toolset bound by its type, with no instance) is re-keyed to the one
instance of that type its tenant has. Anything else is reported and left alone: two or more
candidate instances (``ambiguous``), none (``no_instance``), a candidate the owner already
binds (``duplicate_instance``), or an owner whose tenant is unknown (``tenant_unknown``). An
owner with any unresolved binding is held whole: none of its bindings change, so a definition
is never half-migrated. Reverting undoes exactly what was applied, and only where the binding
still holds the instance the migration gave it (``drifted`` otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.tool_identity.names import type_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

UnresolvedReason = Literal["ambiguous", "no_instance", "duplicate_instance", "tenant_unknown"]
DriftReason = Literal["changed", "gone"]


@dataclass(frozen=True, slots=True)
class DefinitionBinding:
    binding: str
    kind: str
    instance: str | None


@dataclass(frozen=True, slots=True)
class OwnerBindings:
    owner: str
    tenant: str | None
    bindings: tuple[DefinitionBinding, ...]


@dataclass(frozen=True, slots=True)
class InstanceCandidate:
    tenant: str
    kind: str
    instance: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Rekey:
    owner: str
    binding: str
    kind: str
    instance: str
    instance_name: str | None = None


@dataclass(frozen=True, slots=True)
class Unresolved:
    owner: str
    binding: str
    kind: str
    reason: UnresolvedReason
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RekeyPlan:
    rekeys: tuple[Rekey, ...]
    unresolved: tuple[Unresolved, ...]
    held: tuple[str, ...]
    already_keyed: int

    @property
    def changes(self) -> int:
        return len(self.rekeys)


@dataclass(frozen=True, slots=True)
class Drifted:
    rekey: Rekey
    reason: DriftReason


@dataclass(frozen=True, slots=True)
class RevertPlan:
    reverts: tuple[Rekey, ...]
    drifted: tuple[Drifted, ...]
    # Already unkeyed (a revert or an apply that died part-way): nothing to write, the ledger entry goes (QA12-F3).
    settled: tuple[Rekey, ...] = ()


def _candidates_by(candidates: Sequence[InstanceCandidate]) -> dict[tuple[str, str], list[InstanceCandidate]]:
    found: dict[tuple[str, str], list[InstanceCandidate]] = {}
    for candidate in sorted(candidates, key=lambda c: c.instance):
        found.setdefault((candidate.tenant, type_key(candidate.kind)), []).append(candidate)
    return found


def _resolve(
    owner: OwnerBindings,
    binding: DefinitionBinding,
    by_kind: dict[tuple[str, str], list[InstanceCandidate]],
) -> Rekey | Unresolved:
    if owner.tenant is None:
        return Unresolved(owner.owner, binding.binding, binding.kind, "tenant_unknown")
    found = by_kind.get((owner.tenant, type_key(binding.kind)), [])
    ids = tuple(c.instance for c in found)
    if not found:
        return Unresolved(owner.owner, binding.binding, binding.kind, "no_instance")
    if len(found) > 1:
        return Unresolved(owner.owner, binding.binding, binding.kind, "ambiguous", ids)
    bound = {b.instance for b in owner.bindings if b.instance and type_key(b.kind) == type_key(binding.kind)}
    if found[0].instance in bound:
        return Unresolved(owner.owner, binding.binding, binding.kind, "duplicate_instance", ids)
    return Rekey(owner.owner, binding.binding, binding.kind, found[0].instance, found[0].name)


def plan_rekeys(owners: Sequence[OwnerBindings], candidates: Sequence[InstanceCandidate]) -> RekeyPlan:
    by_kind = _candidates_by(candidates)
    rekeys: list[Rekey] = []
    unresolved: list[Unresolved] = []
    held: list[str] = []
    already = 0
    for owner in sorted(owners, key=lambda o: o.owner):
        legacy = sorted((b for b in owner.bindings if not b.instance), key=lambda b: b.binding)
        already += len(owner.bindings) - len(legacy)
        outcomes = [_resolve(owner, binding, by_kind) for binding in legacy]
        failed = [o for o in outcomes if isinstance(o, Unresolved)]
        if failed:
            unresolved.extend(failed)
            held.append(owner.owner)
            continue
        rekeys.extend(o for o in outcomes if isinstance(o, Rekey))
    return RekeyPlan(tuple(rekeys), tuple(unresolved), tuple(held), already)


def plan_reverts(applied: Sequence[Rekey], current: Mapping[str, str | None]) -> RevertPlan:
    """``current``: each applied binding's instance now (absent: the binding is gone)."""
    reverts: list[Rekey] = []
    drifted: list[Drifted] = []
    settled: list[Rekey] = []
    for rekey in applied:
        if rekey.binding not in current:
            drifted.append(Drifted(rekey, "gone"))
        elif current[rekey.binding] is None:
            settled.append(rekey)
        elif current[rekey.binding] != rekey.instance:
            drifted.append(Drifted(rekey, "changed"))
        else:
            reverts.append(rekey)
    return RevertPlan(tuple(reverts), tuple(drifted), tuple(settled))


__all__ = [
    "DefinitionBinding",
    "DriftReason",
    "Drifted",
    "InstanceCandidate",
    "OwnerBindings",
    "Rekey",
    "RekeyPlan",
    "RevertPlan",
    "Unresolved",
    "UnresolvedReason",
    "plan_rekeys",
    "plan_reverts",
]
