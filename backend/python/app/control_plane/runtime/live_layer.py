"""The live layer on the Python side (02-lld §3.1, §8.3; NFR-23, NFR-24, NFR-33).

An org's live layer is what running work is narrowed by while it runs. Node owns it and answers it on the internal
live route (``LiveLayerSource``); ``LiveLayerCache`` holds it per org so a decision reads it in process. A change
event names the org and its new revision, never a value (QA14-N5), so an event marks the org and the next read
reloads it; without events the layer is reloaded once its time to live has passed. A layer that cannot be reloaded
is answered as it was while it is younger than the time to live, and ``stale`` after that: what reads it then fails
closed for writes.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Protocol, cast

from app.control_plane.runtime.revisions import change_step
from app.core.org_context import for_org, spawn_detached
from harness_core.access.vendor_rules import OrgAccessPolicy
from harness_core.contracts import dump_contract
from harness_core.logs import HarnessLog
from harness_core.models.placement import PinInvalid
from harness_core.policy.orders import InvalidPolicyValue
from harness_core.policy.registry import ENGINE_PROFILE
from harness_core.policy.resolver import DimensionPolicyResolver
from harness_core.policy.types import EMPTY_PREFIX, FoldedPrefix
from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from app.control_plane.contracts.generated.change_event import ChangeEvent
    from app.control_plane.contracts.generated.live_layer import (
        LiveLayer as LiveLayerDocument,
    )
    from harness_core.clock import Clock

LIVE_TTL_S: Final = 30.0
# The ``models`` members that narrow model access rather than name routes (with the model regions' pin members).
_ACCESS_FIELDS: Final = frozenset({"disabledRules", "consumerPlans"})
# How long a source that just failed is left alone: an outage must not put a request on every decision (NFR-7).
LIVE_RETRY_S: Final = 5.0
_NO_REVISION: Final = -1


class LiveLayerUnavailable(HarnessRefusal):
    """The live route did not answer a layer the cache can use."""

    reason_code = "live_layer_unavailable"


class LiveLayerSource(Protocol):
    async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
        """The org's live layer; ``None`` when its revision is still ``since``. Raises ``LiveLayerUnavailable``."""
        ...


@dataclass(frozen=True, slots=True)
class LiveLayer:
    """One org's live layer at a head revision, folded: ``org`` narrows every run, ``agents[key]`` (folded onto
    ``org``) that agent's runs. ``access`` is the org's model-access narrowing, which is not a dimension.
    ``stale``: it could not be reloaded for longer than its time to live (NFR-33)."""

    org_id: str
    revision: int
    org: FoldedPrefix = EMPTY_PREFIX
    agents: Mapping[str, FoldedPrefix] = field(default_factory=dict[str, FoldedPrefix])
    revoked_bindings: frozenset[str] = frozenset()
    access: OrgAccessPolicy | None = None
    # The ``engineProfile`` section of each layer that narrows every run of the org, as stored (the org's engine
    # ceiling): what a new agent's profile is checked against, where the folded dimensions serve a run.
    engine_ceilings: tuple[Mapping[str, JsonValue], ...] = ()
    # What an edition carries beside the layers (``live-layer.schema.json`` extended by its overlay), as stored, by
    # member name; empty in this build.
    carried: Mapping[str, JsonValue] = field(default_factory=dict["str", "JsonValue"])
    # Allowed websites as the deployment's, the org's and each agent's layers hold them (WP-63), as stored: read per
    # run by ``app.harness.websites.live_sites``; ``None``: no layer sets any.
    websites: Mapping[str, JsonValue] | None = None
    # Allowed git hosts for repository_fetch as the org's and each agent's layers hold them (WP-63 part A), as stored:
    # folded per call by ``app.harness.repositories.live_repository_hosts``; ``None``: no layer sets any.
    repositories: Mapping[str, JsonValue] | None = None
    stale: bool = False

    def for_agent(self, agent_key: str | None) -> FoldedPrefix:
        return self.org if agent_key is None else self.agents.get(agent_key, self.org)


# The live layer's own members (``contracts/control-plane/live-layer.schema.json`` as this repository writes it);
# any other member of a document is one an edition's overlay adds, kept in ``LiveLayer.carried``.
LIVE_LAYER_MEMBERS: Final = frozenset(
    {"schemaVersion", "orgId", "revision", "layers", "websites", "repositories", "revokedBindings"}
)


def _access(layers: list[Mapping[str, JsonValue]]) -> OrgAccessPolicy | None:
    """Model-access rows switched off by any layer stay off; consumer plans need every layer that names them; each
    layer's region pin binds (MG-15: a route must be allowed by every one)."""
    from app.utils.model_placement import model_regions

    regions = model_regions()
    named = [cast("Mapping[str, JsonValue]", layer["models"]) for layer in layers if "models" in layer]
    named = [models for models in named if (_ACCESS_FIELDS | regions.pin_fields) & models.keys()]
    if not named:
        return None
    disabled = frozenset(rule for models in named for rule in cast("list[str]", models.get("disabledRules", [])))
    plans = [models["consumerPlans"] for models in named if "consumerPlans" in models]
    try:
        pins = tuple(pin for models in named if (pin := regions.pin_of(models)))
    except PinInvalid as invalid:
        raise InvalidPolicyValue(f"models: {invalid}") from None
    return OrgAccessPolicy(disabled, consumer_plans=bool(plans) and all(plans), region_pins=pins)


def folded_live_layer(document: LiveLayerDocument, resolver: DimensionPolicyResolver | None = None) -> LiveLayer:
    """``document`` as the runtime reads it. Raises ``InvalidPolicyValue`` for a value an order cannot read: a
    layer is applied whole or not at all."""
    resolver = resolver if resolver is not None else DimensionPolicyResolver()
    org_layers: list[Mapping[str, JsonValue]] = []
    by_agent: dict[str, list[Mapping[str, JsonValue]]] = {}
    for scoped in document.layers:
        values = cast("Mapping[str, JsonValue]", dump_contract(scoped.layer))
        if scoped.agent_key is None:
            org_layers.append(values)
        else:
            by_agent.setdefault(scoped.agent_key, []).append(values)
    org = resolver.live_fold(org_layers)
    return LiveLayer(
        org_id=document.org_id.root,
        revision=document.revision.root,
        org=org,
        agents={key: resolver.live_fold(layers, org) for key, layers in by_agent.items()},
        revoked_bindings=frozenset(ref.root for ref in document.revoked_bindings),
        access=_access(org_layers),
        engine_ceilings=tuple(
            cast("Mapping[str, JsonValue]", layer[ENGINE_PROFILE]) for layer in org_layers if ENGINE_PROFILE in layer
        ),
        carried={
            name: value
            for name, value in cast("Mapping[str, JsonValue]", dump_contract(document)).items()
            if name not in LIVE_LAYER_MEMBERS
        },
        websites=None if document.websites is None else cast("Mapping[str, JsonValue]", dump_contract(document.websites)),
        repositories=None
        if document.repositories is None
        else cast("Mapping[str, JsonValue]", dump_contract(document.repositories)),
    )


@dataclass(slots=True)
class _Held:
    layer: LiveLayer
    # Clock time the source last answered for it; ``None``: it never has.
    confirmed_at: float | None
    # Raised by every event that names the org; a reload clears only the marks it saw.
    marks: int = 0
    seen: int = 0
    failed_at: float | None = None
    reload: asyncio.Task[None] | None = None


class ReloadingLiveLayerCache:
    """``LiveLayerCache`` over a ``LiveLayerSource``: each org's live layer, at most ``bound`` of them (least
    recently read out first)."""

    def __init__(
        self,
        source: LiveLayerSource,
        clock: Clock,
        *,
        ttl_s: float = LIVE_TTL_S,
        bound: int = 1000,
        resolver: DimensionPolicyResolver | None = None,
        log: HarnessLog | None = None,
        retry_s: float = LIVE_RETRY_S,
    ) -> None:
        self._source = source
        self._clock = clock
        self._ttl_s = ttl_s
        self._retry_s = min(retry_s, ttl_s)
        self._bound = bound
        self._resolver = resolver if resolver is not None else DimensionPolicyResolver()
        self._log = log if log is not None else HarnessLog.named("app.control_plane.live_layer")
        self._held: OrderedDict[str, _Held] = OrderedDict()

    async def current(self, org_id: str) -> LiveLayer:
        held = self._held.get(org_id)
        if held is None:
            held = _Held(LiveLayer(org_id, _NO_REVISION), None)
            self._held[org_id] = held
            while len(self._held) > self._bound:
                _ = self._held.popitem(last=False)
        else:
            self._held.move_to_end(org_id)
        if self._due(held):
            if held.reload is None:
                # Detached and bound to the org it reloads: the readers who wait on it are not its context.
                held.reload = spawn_detached(self._reload_bound(org_id, held))
            # Shielded: one reader giving up must not cancel the reload the others wait on.
            await asyncio.shield(held.reload)
        return held.layer if self._fresh(held) else replace(held.layer, stale=True)

    def on_change(self, event: ChangeEvent) -> None:
        """Applies the revision rule: a duplicate is ignored; the next event or a gap marks the org, and its next
        read reloads it (the event names what changed, never its value)."""
        held = self._held.get(event.org_id)
        if held is None:
            return
        step = change_step(held.layer.revision, revision=event.revision, previous=event.previous)
        if step == "duplicate":
            return
        held.marks += 1
        held.failed_at = None

    @property
    def held(self) -> int:
        return len(self._held)

    def _fresh(self, held: _Held) -> bool:
        return held.confirmed_at is not None and self._clock.monotonic() - held.confirmed_at < self._ttl_s

    def _due(self, held: _Held) -> bool:
        if held.reload is not None:
            return True
        if held.failed_at is not None and self._clock.monotonic() - held.failed_at < self._retry_s:
            return False
        return held.marks != held.seen or not self._fresh(held)

    async def _reload_bound(self, org_id: str, held: _Held) -> None:
        with for_org(org_id, job="live-layer-reload"):
            await self._reload(org_id, held)

    async def _reload(self, org_id: str, held: _Held) -> None:
        marks = held.marks
        # Only an unmarked layer may be confirmed as unchanged: a marked one is known to be behind.
        since = held.layer.revision if marks == held.seen and held.confirmed_at is not None else None
        try:
            document = await self._source.load(org_id, since)
            layer = held.layer if document is None else folded_live_layer(document, self._resolver)
        except (LiveLayerUnavailable, InvalidPolicyValue) as refused:
            held.failed_at = self._clock.monotonic()
            code = refused.reason_code if isinstance(refused, LiveLayerUnavailable) else "live_layer_invalid"
            self._log.warning(
                "live_layer.reload_failed",
                reasonCode=code,
                orgId=org_id,
                heldRevision=held.layer.revision,
                choice="held" if self._fresh(held) else "stale",
            )
        else:
            held.layer = layer
            held.confirmed_at = self._clock.monotonic()
            held.seen = marks
            held.failed_at = None
        finally:
            held.reload = None


__all__ = [
    "LIVE_RETRY_S",
    "LIVE_TTL_S",
    "LiveLayer",
    "LiveLayerSource",
    "LiveLayerUnavailable",
    "ReloadingLiveLayerCache",
    "folded_live_layer",
]
