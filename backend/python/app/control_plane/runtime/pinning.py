"""The pin a ``RunConfigPinner`` answers (02-lld §2.9, §5.3), built the same way in both languages.

PipesHub pins in Node; this module is what a Loom host or a test pins with, and what the shared vectors
(``contracts/control-plane/vectors/resolved-run-config.json``) hold Node's pinner to: the same layers resolve to the
same ``EffectiveRunConfig``, and the same pin fields to the same digest.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, cast

from app.control_plane.contracts.generated.errors import ERROR_CODES, ErrorCode
from app.control_plane.contracts.generated.problem import Problem
from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from harness_core.contracts import canonical_digest, dump_contract
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.isolation import OVERRIDE_ENV, override_on, release_floor
from harness_core.policy.registry import policy_registry
from harness_core.policy.types import ScopedLayer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.policy.types import PolicyRefusal

Surface = Literal["web", "slack", "mcp", "schedule", "child", "next_run"]

# The layers a pin folds below the release, top to bottom (02-lld §3.1); `live` is CPD-6's, applied by the worker.
PIN_CHAIN: Final = (
    "deployment",
    "org",
    "team",
    "agent",
    "user",
    "trigger",
    "parent",
    "request",
)

REFUSAL_CODES: Final[Mapping[str, ErrorCode]] = {
    "widening": "CP_WIDENING",
    "not_exposed": "CP_NOT_EXPOSED",
    "outside_ceiling": "CP_OUTSIDE_CEILING",
    "locked": "CP_LOCKED",
    "invalid": "CP_INVALID_CONFIG",
}


@dataclass(frozen=True, slots=True)
class PinSubject:
    """Who a run is for and what it asks, decided by the server (03-interfaces §4.4)."""

    org_id: str
    actor_id: str
    agent_key: str | None
    chat_mode: str
    surface: Surface
    request: Mapping[str, JsonValue]
    project_id: str | None = None
    trigger: Mapping[str, JsonValue] | None = None
    parent: ResolvedRunConfig | None = None
    # The user who owns the agent, from the definition the root's admission loaded.
    agent_owner: str | None = None


@dataclass(frozen=True, slots=True)
class PinSnapshot:
    """What one pin reads, loaded once: the org head and the stored layers by scope (``org:o1``, ``agent:a7``)."""

    head_revision: int
    layers: Mapping[str, Mapping[str, JsonValue]] = field(
        default_factory=dict[str, "Mapping[str, JsonValue]"]
    )
    deployment: Mapping[str, JsonValue] | None = None


def release_layer() -> ScopedLayer:
    """The release layer, at this install's engine isolation floor: gVisor, or the documented dev/test override's
    (``harness_core.engines.isolation``), as Node's ``releaseLayers`` pins it."""
    values = cast("dict[str, JsonValue]", dump_contract(RELEASE_DEFAULTS))
    floor = release_floor(override=override_on(os.environ.get(OVERRIDE_ENV)))
    values["engineProfile"] = {**cast("dict[str, JsonValue]", values["engineProfile"]), "isolationMin": floor.value}
    return ScopedLayer("release", values)


PARENT: Final = "parent"


def parent_config_layer(config: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """A parent's resolved values (an ``EffectiveRunConfig``) as a ``parent`` layer: every dimension a layer
    carries at its own path and the table exposes to ``parent``. The rest (the rollout stages) is not a layer's to
    set and reaches the child through ``inherited_dimensions``."""
    layer: dict[str, JsonValue] = {"schemaVersion": 1, "scope": {"kind": PARENT}}
    dimensions = cast("Mapping[str, Mapping[str, JsonValue]]", config["dimensions"])
    for dimension in policy_registry().all():
        if (
            dimension.layer_path != dimension.path
            or dimension.path not in dimensions
            or PARENT not in dimension.exposure
        ):
            continue
        keys = dimension.path.split(".")
        node: dict[str, JsonValue] = layer
        for key in keys[:-1]:
            node = cast("dict[str, JsonValue]", node.setdefault(key, {}))
        node[keys[-1]] = dimensions[dimension.path]["value"]
    return layer


def parent_layer(parent: ResolvedRunConfig) -> dict[str, JsonValue]:
    return parent_config_layer(cast("Mapping[str, JsonValue]", dump_contract(parent.effective_config)))


def inherited_dimensions(child: Mapping[str, JsonValue], parent: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """A child's resolved config with what its parent's pin holds and no ``parent`` layer may carry: the parent's
    entries as they are, in place of the release's where the release names the row. A child is part of its parent's
    run, so it runs on the stages and the routing lists its root was admitted on. An entry the ``parent`` layer set
    keeps where its value came from (``with_parent_ceilings``)."""
    parents = cast("Mapping[str, Mapping[str, JsonValue]]", parent["dimensions"])
    own = {
        path: with_parent_ceilings(entry, parents.get(path))
        for path, entry in cast("Mapping[str, Mapping[str, JsonValue]]", child["dimensions"]).items()
    }
    exposed = {d.path for d in policy_registry().all() if PARENT in d.exposure}
    inherited = {path: entry for path, entry in parents.items() if path not in exposed}
    return {**child, "dimensions": {**own, **inherited}}


# At most this many ceilings to an entry (effective-run-config.schema.json).
MAX_CEILINGS: Final = 10


def with_parent_ceilings(
    entry: Mapping[str, JsonValue], parent_entry: Mapping[str, JsonValue] | None
) -> Mapping[str, JsonValue]:
    """The parent's ceilings below its release before the ``parent`` ceiling of a child's entry (the child folds under
    the same release; an ancestor's own ``parent`` ceilings restate what precedes them). The ``parent`` layer carries
    only the value, and readers take meaning from the layers that set it: whose rule a child's approval card names
    (QA6-F6), which engine classes are the org's rule (QA10-F3). Without them a deep child's card named the org's
    ``writes`` rule the deployment's (QAFINAL-FU-PW107). Node's ``withParentCeilings`` is the same rule."""
    ceilings = cast("list[Mapping[str, JsonValue]]", entry["ceilings"])
    at = next((i for i, c in enumerate(ceilings) if c["layer"] == PARENT), None)
    if at is None or parent_entry is None:
        return entry
    provenance = [
        c for c in cast("list[Mapping[str, JsonValue]]", parent_entry["ceilings"]) if c["layer"] not in ("release", PARENT)
    ]
    if not provenance or len(ceilings) + len(provenance) > MAX_CEILINGS:
        return entry
    return {**entry, "ceilings": [*ceilings[:at], *copy.deepcopy(provenance), *ceilings[at:]]}


def pin_layers(
    subject: PinSubject, snapshot: PinSnapshot, agent_owner_team: str | None = None
) -> list[ScopedLayer]:
    """The chain a pin folds, release first; a layer that is absent is skipped."""
    by_kind: dict[str, Mapping[str, JsonValue] | None] = {
        "deployment": snapshot.deployment,
        "org": snapshot.layers.get(f"org:{subject.org_id}"),
        "team": snapshot.layers.get(f"team:{agent_owner_team}")
        if agent_owner_team is not None
        else None,
        "agent": snapshot.layers.get(f"agent:{subject.agent_key}")
        if subject.agent_key is not None
        else None,
        "user": snapshot.layers.get(f"user:{subject.actor_id}"),
        "trigger": subject.trigger,
        "parent": parent_layer(subject.parent) if subject.parent is not None else None,
        "request": subject.request,
    }
    return [release_layer()] + [
        ScopedLayer(kind, values)
        for kind in PIN_CHAIN
        if (values := by_kind[kind]) is not None
    ]


def pinned_agent_owner(subject: PinSubject) -> str | None:
    """The agent's owner a pin is resolved for: a root's as admission read it, a child's as its parent's pin holds it
    (a child never re-reads the definition, so what the owner shared would otherwise read ``not_visible`` in it)."""
    if subject.parent is None:
        return subject.agent_owner
    return subject.parent.scope.agent_owner


def pin_scope(subject: PinSubject) -> dict[str, JsonValue]:
    """A pin's ``scope``; Node's ``pinScope`` twin. ``agentOwner`` is absent, not null, so a pin without one keeps
    its digest."""
    owner = pinned_agent_owner(subject)
    return {
        "agentKey": subject.agent_key,
        "projectId": subject.project_id,
        **({} if owner is None else {"agentOwner": owner}),
    }


def pin_digest(pin: Mapping[str, JsonValue]) -> str:
    """The pin's identity: the canonical digest of every member but ``digest``."""
    return canonical_digest(
        {key: value for key, value in pin.items() if key != "digest"}
    )


def refusal_problem(refusal: PolicyRefusal) -> Problem:
    code = REFUSAL_CODES[refusal.code]
    entry = ERROR_CODES[code]
    status = entry.status.root if entry.status is not None else 422
    return Problem.model_validate(
        {
            "code": code,
            "status": status,
            "message": refusal.detail,
            "retryable": entry.retryable,
            "dimension": refusal.dimension,
            "layer": refusal.layer,
        }
    )


def assemble(fields: Mapping[str, JsonValue]) -> ResolvedRunConfig:
    """A pin from its members (without ``digest``), its digest computed here."""
    pin = {**fields, "digest": pin_digest(fields)}
    return ResolvedRunConfig.model_validate(pin)


__all__ = [
    "PIN_CHAIN",
    "PinSnapshot",
    "PinSubject",
    "Surface",
    "assemble",
    "inherited_dimensions",
    "parent_config_layer",
    "parent_layer",
    "pin_digest",
    "pin_layers",
    "refusal_problem",
    "release_layer",
    "with_parent_ceilings",
]
