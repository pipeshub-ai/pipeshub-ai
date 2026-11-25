"""Contract suites of the Python runtime ports of the control plane (03-interfaces §3.2, §4.5, §5.2, §5.4).

Subclass one and provide its fixture; ``tests/unit/control_plane/test_runtime_ports.py`` runs each on its in-memory
adapter, and the WP that builds a production adapter runs the same suite on it (CP-SKEL §3).

- ``ItemRuntimeContract``: ``item_runtime`` (an ``ItemRuntimeHarness``): the runtime, its kind, and how many sessions
  it holds open (``open_sessions``).
- ``ItemRuntimeRegistryContract``: ``registry_factory``, a callable returning an empty registry.
- ``CredentialBrokerContract``: ``broker_harness`` (a ``BrokerHarness``): the broker, the clock it reads, and the
  vault side a binder and refresher write (``store``, ``tombstone``).
- ``ChangeNotifierContract``: ``notifier_harness`` (a ``NotifierHarness``): the notifier and ``settled()``.
- ``LiveLayerCacheContract``: ``live_harness`` (a ``LiveHarness``): a cache of bound 2 and a 30 s time to live over
  ``backstop`` (what the internal live route answers), the clock it reads and how many backstop loads ran.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from app.control_plane.contracts.generated.change_event import ChangeEvent
from app.control_plane.contracts.generated.effective_set import (
    EffectiveItem,
    PinnedBinding,
)
from app.control_plane.runtime.types import LiveDescriptor, RunContext
from harness_core.credentials.errors import (
    CredentialPurposeMismatch,
    CredentialRefused,
    CredentialRevoked,
    CredentialUnknown,
)
from harness_core.credentials.types import CredentialPurpose
from harness_core.driver.run_driver import CapabilityLost

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from app.control_plane.runtime.live_layer import LiveLayer
    from app.control_plane.runtime.ports import (
        ChangeNotifier,
        CredentialBroker,
        ItemRuntime,
        ItemRuntimeRegistry,
        LiveLayerCache,
    )
    from harness_core.testing.clock import ManualClock

ORG = "org-a"
OTHER_ORG = "org-b"
BINDING = "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0001"
OTHER_BINDING = "bnd_01J9ZQ5Y8K3M4N5P6Q7R8S0002"
PURPOSE = CredentialPurpose(ORG, "jira@inst-1", "run-1", "attempt-1")
MATERIAL = "runtime-contract-secret-5b7f0c2e9d14"
REFRESHED = "runtime-contract-secret-refreshed-77aa"


def digest(fill: str) -> str:
    """A digest of one repeated hex digit."""
    return f"sha256:{(fill * 64)[:64]}"


def tool_digest(name: str) -> str:
    return f"sha256:{hashlib.sha256(name.encode()).hexdigest()}"


def effective_item(kind: str, installation: str, tools: list[str], *, ready: bool = True) -> EffectiveItem:
    return EffectiveItem.model_validate(
        {
            "installation": installation,
            "item": f"{kind}:pipeshub/{installation.lower().replace('_', '-')}@1",
            "kind": kind,
            "state": "ready" if ready else "unavailable",
            "reason": None if ready else "credential_missing",
            "tools": [{"toolId": f"{kind}@{installation}/{name}", "descriptorDigest": tool_digest(name)} for name in tools]
            if ready
            else [],
            "binding": None,
            "provenance": {"grantedBy": "agent:a7", "visibleVia": "org", "approvedBy": None},
        }
    )


def pinned_binding(ref: str = BINDING, version_digest: str = digest("1")) -> PinnedBinding:
    return PinnedBinding.model_validate({"ref": ref, "owner": "user:u3", "versionDigest": version_digest})


def change_event(org_id: str, revision: int, *, previous: int | None = None, revoked: list[str] | None = None) -> ChangeEvent:
    narrowing = revoked is not None
    return ChangeEvent.model_validate(
        {
            "type": "cp.changed",
            "orgId": org_id,
            "revision": revision,
            "previous": revision - 1 if previous is None else previous,
            "touched": [{"kind": "binding", "id": (revoked or [BINDING])[0]}],
            "narrowing": narrowing,
            **({"live": {"revokedBindings": revoked}} if narrowing else {}),
        }
    )


class ItemRuntimeHarness(Protocol):
    @property
    def runtime(self) -> ItemRuntime: ...

    @property
    def credentials(self) -> CredentialBroker: ...

    def open_sessions(self) -> int: ...


class ItemRuntimeContract:
    @pytest.fixture
    def item_runtime(self) -> ItemRuntimeHarness:
        raise NotImplementedError("provide an item_runtime fixture")

    async def test_ir1_loads_exactly_the_pinned_tools_of_ready_items(self, item_runtime: ItemRuntimeHarness) -> None:
        h = item_runtime
        kind = h.runtime.kind
        items = [effective_item(kind, "inst-a", ["list", "create"]), effective_item(kind, "inst-b", ["search"])]
        loaded = await h.runtime.load(items, RunContext(ORG, "run-1", "attempt-1"), h.credentials)
        pinned = {(t.tool_id, t.descriptor_digest.root) for item in items for t in item.tools}
        assert {(t.tool_id, t.descriptor_digest) for t in loaded.tools} == pinned
        assert len(loaded.tools) == len(pinned)
        await loaded.close()

    async def test_ir2_unavailable_items_are_never_loaded(self, item_runtime: ItemRuntimeHarness) -> None:
        h = item_runtime
        kind = h.runtime.kind
        items = [effective_item(kind, "inst-a", ["list"]), effective_item(kind, "inst-b", ["search"], ready=False)]
        loaded = await h.runtime.load(items, RunContext(ORG, "run-1"), h.credentials)
        assert [t.tool_id for t in loaded.tools] == [f"{kind}@inst-a/list"]
        assert h.open_sessions() == 1
        await loaded.close()

    def test_ir3_verify_flags_a_changed_descriptor(self, item_runtime: ItemRuntimeHarness) -> None:
        kind = item_runtime.runtime.kind
        item = effective_item(kind, "inst-a", ["list"])
        tool_id = f"{kind}@inst-a/list"
        assert item_runtime.runtime.verify(item, LiveDescriptor(tool_id, tool_digest("list"))) is None
        changed = item_runtime.runtime.verify(item, LiveDescriptor(tool_id, digest("9")))
        assert changed is not None
        assert changed.code == "CP_DESCRIPTOR_CHANGED"
        assert changed.subject is not None and changed.subject.installation is not None

    async def test_ir4_closing_a_load_closes_what_it_opened(self, item_runtime: ItemRuntimeHarness) -> None:
        h = item_runtime
        kind = h.runtime.kind
        before = h.open_sessions()
        items = [effective_item(kind, "inst-a", ["list"]), effective_item(kind, "inst-b", ["search"])]
        loaded = await h.runtime.load(items, RunContext(ORG, "run-1"), h.credentials)
        assert h.open_sessions() > before
        await loaded.close()
        assert h.open_sessions() == before


class ItemRuntimeRegistryContract:
    @pytest.fixture
    def registry_factory(self) -> Callable[[], ItemRuntimeRegistry]:
        raise NotImplementedError("provide a registry_factory fixture")

    @staticmethod
    def _runtime(kind: str) -> ItemRuntime:
        from app.control_plane.testing.memory import InMemoryItemRuntime

        return InMemoryItemRuntime(kind)

    def test_ir5_an_unregistered_kind_is_a_lost_capability(
        self, registry_factory: Callable[[], ItemRuntimeRegistry]
    ) -> None:
        registry = registry_factory()
        mcp = self._runtime("mcp")
        registry.register(mcp)
        assert registry.for_kind("mcp") is mcp
        with pytest.raises(CapabilityLost) as lost:
            _ = registry.for_kind("plugin")
        assert lost.value.missing == ("plugin",)

    def test_ir5_a_second_runtime_for_a_kind_is_refused(
        self, registry_factory: Callable[[], ItemRuntimeRegistry]
    ) -> None:
        registry = registry_factory()
        first = self._runtime("mcp")
        registry.register(first)
        with pytest.raises(ValueError, match="mcp"):
            registry.register(self._runtime("mcp"))
        assert registry.for_kind("mcp") is first


class BrokerHarness(Protocol):
    @property
    def broker(self) -> CredentialBroker: ...

    @property
    def clock(self) -> ManualClock: ...

    def store(self, binding: str, material: JsonValue) -> str:
        """A new current version of ``binding`` (the binder's write or a refresh); answers the digest the vault gave it."""
        ...

    def tombstone(self, binding: str) -> None: ...


class CredentialBrokerContract:
    @pytest.fixture
    def broker_harness(self) -> BrokerHarness:
        raise NotImplementedError("provide a broker_harness fixture")

    async def test_cbr1_a_lease_resolves_to_the_pinned_bindings_secret(self, broker_harness: BrokerHarness) -> None:
        h = broker_harness
        pinned = h.store(BINDING, MATERIAL)
        _ = h.store(OTHER_BINDING, "someone-elses-secret")
        lease = await h.broker.lease(pinned_binding(version_digest=pinned), PURPOSE, 300)
        assert (await h.broker.resolve(lease, PURPOSE)).material == MATERIAL

    async def test_cbr2_a_tombstone_wins_over_the_pin_and_over_an_earlier_lease(self, broker_harness: BrokerHarness) -> None:
        h = broker_harness
        pinned = h.store(BINDING, MATERIAL)
        earlier = await h.broker.lease(pinned_binding(version_digest=pinned), PURPOSE, 300)
        h.tombstone(BINDING)
        with pytest.raises(CredentialRevoked):
            _ = await h.broker.lease(pinned_binding(version_digest=pinned), PURPOSE, 300)
        with pytest.raises(CredentialRevoked):
            _ = await h.broker.resolve(earlier, PURPOSE)

    async def test_cbr3_a_refresh_keeps_the_pin_and_a_foreign_digest_is_refused(self, broker_harness: BrokerHarness) -> None:
        h = broker_harness
        pinned = h.store(BINDING, MATERIAL)
        refreshed = h.store(BINDING, REFRESHED)
        assert refreshed != pinned
        lease = await h.broker.lease(pinned_binding(version_digest=pinned), PURPOSE, 300)
        assert (await h.broker.resolve(lease, PURPOSE)).material == REFRESHED
        foreign = h.store(OTHER_BINDING, "someone-elses-secret")
        for unknown in (foreign, digest("9")):
            with pytest.raises(CredentialRefused):
                _ = await h.broker.lease(pinned_binding(version_digest=unknown), PURPOSE, 300)

    async def test_cbr4_another_purpose_is_refused(self, broker_harness: BrokerHarness) -> None:
        h = broker_harness
        lease = await h.broker.lease(pinned_binding(version_digest=h.store(BINDING, MATERIAL)), PURPOSE, 300)
        for other in (
            CredentialPurpose(ORG, "jira@inst-1", "run-2", "attempt-1"),
            CredentialPurpose(OTHER_ORG, "jira@inst-1", "run-1", "attempt-1"),
            CredentialPurpose(ORG, "slack@inst-2", "run-1", "attempt-1"),
        ):
            with pytest.raises(CredentialPurposeMismatch):
                _ = await h.broker.resolve(lease, other)

    async def test_cbr5_no_secret_in_reprs_or_logs(
        self, broker_harness: BrokerHarness, caplog: pytest.LogCaptureFixture
    ) -> None:
        h = broker_harness
        caplog.set_level(logging.DEBUG)
        lease = await h.broker.lease(pinned_binding(version_digest=h.store(BINDING, MATERIAL)), PURPOSE, 300)
        secret = await h.broker.resolve(lease, PURPOSE)
        for text in (lease.handle, repr(lease), repr(secret), str(secret), caplog.text):
            assert MATERIAL not in text

    async def test_cbr6_a_released_lease_no_longer_resolves(self, broker_harness: BrokerHarness) -> None:
        h = broker_harness
        lease = await h.broker.lease(pinned_binding(version_digest=h.store(BINDING, MATERIAL)), PURPOSE, 300)
        await h.broker.release(lease)
        with pytest.raises(CredentialUnknown):
            _ = await h.broker.resolve(lease, PURPOSE)


class NotifierHarness(Protocol):
    @property
    def notifier(self) -> ChangeNotifier: ...

    async def settled(self) -> None: ...


def _seen(events: list[ChangeEvent]) -> list[str]:
    return [f"{e.org_id}@{e.revision}" for e in events]


class ChangeNotifierContract:
    @pytest.fixture
    def notifier_harness(self) -> NotifierHarness:
        raise NotImplementedError("provide a notifier_harness fixture")

    async def test_cn1_every_subscriber_gets_every_event_in_order_per_org(self, notifier_harness: NotifierHarness) -> None:
        h = notifier_harness
        first: list[ChangeEvent] = []
        second: list[ChangeEvent] = []
        _ = h.notifier.subscribe(first.append)
        _ = h.notifier.subscribe(second.append)
        published = [change_event(ORG, 1), change_event(OTHER_ORG, 7), change_event(ORG, 2), change_event(ORG, 3)]
        for event in published:
            await h.notifier.publish(event)
        await h.settled()
        for got in (first, second):
            assert _seen([e for e in got if e.org_id == ORG]) == [f"{ORG}@1", f"{ORG}@2", f"{ORG}@3"]
            assert _seen([e for e in got if e.org_id == OTHER_ORG]) == [f"{OTHER_ORG}@7"]
        assert first[0] == published[0]

    async def test_cn2_an_unsubscribed_handler_gets_nothing_more(self, notifier_harness: NotifierHarness) -> None:
        h = notifier_harness
        got: list[ChangeEvent] = []
        unsubscribe = h.notifier.subscribe(got.append)
        await h.notifier.publish(change_event(ORG, 1))
        await h.settled()
        unsubscribe()
        await h.notifier.publish(change_event(ORG, 2))
        await h.settled()
        assert _seen(got) == [f"{ORG}@1"]

    async def test_cn3_a_late_subscriber_is_not_replayed(self, notifier_harness: NotifierHarness) -> None:
        h = notifier_harness
        await h.notifier.publish(change_event(ORG, 1))
        await h.settled()
        late: list[ChangeEvent] = []
        _ = h.notifier.subscribe(late.append)
        await h.notifier.publish(change_event(ORG, 2))
        await h.settled()
        assert _seen(late) == [f"{ORG}@2"]


class LiveBackstop(Protocol):
    """What the internal live route answers, as a test sets it (``InMemoryLiveLayerSource`` is one)."""

    unreachable: bool

    def put(self, org_id: str, revision: int, *layers: JsonValue, revoked: Sequence[str] = ()) -> None: ...


class LiveHarness(Protocol):
    @property
    def cache(self) -> LiveLayerCache: ...

    @property
    def backstop(self) -> LiveBackstop: ...

    @property
    def clock(self) -> ManualClock: ...

    def loads(self) -> int: ...


def _writes(level: str, agent_key: str | None = None) -> JsonValue:
    scoped: dict[str, JsonValue] = {"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "approvals": {"writes": level}}}
    if agent_key is not None:
        scoped["agentKey"] = agent_key
    return scoped


def _write_level(layer: LiveLayer, agent_key: str | None = None) -> str | None:
    entry = layer.for_agent(agent_key).entries.get("approvals")
    return None if entry is None else cast("Mapping[str, Mapping[str, str]]", entry.value)["classes"]["write"]


class LiveLayerCacheContract:
    """LL-1..LL-5 as CPD-6 amended them: a change event names what changed and never its value (QA14-N5), so an
    event marks the org and its next read reloads it from the route, where CP-SKEL applied a delta from the event."""

    @pytest.fixture
    def live_harness(self) -> LiveHarness:
        raise NotImplementedError("provide a live_harness fixture (bound 2, time to live 30 s)")

    async def test_ll1_an_event_for_a_held_org_reloads_it_before_its_next_read(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10)
        assert _write_level(await h.cache.current(ORG)) is None
        h.backstop.put(ORG, 11, _writes("deny"), revoked=[BINDING])
        h.cache.on_change(change_event(ORG, 11))
        layer = await h.cache.current(ORG)
        assert (layer.revision, _write_level(layer), layer.revoked_bindings, layer.stale) == (
            11, "deny", frozenset({BINDING}), False,
        )
        assert h.loads() == 2

    async def test_ll2_a_duplicate_and_an_event_for_an_org_not_held_cost_nothing(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10, _writes("ask"))
        _ = await h.cache.current(ORG)
        h.cache.on_change(change_event(ORG, 10))
        h.cache.on_change(change_event(ORG, 9))
        h.cache.on_change(change_event(OTHER_ORG, 4))
        layer = await h.cache.current(ORG)
        assert (layer.revision, _write_level(layer)) == (10, "ask")
        assert h.loads() == 1

    async def test_ll3_a_gap_reloads_the_org_and_leaves_another_alone(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10)
        h.backstop.put(OTHER_ORG, 3)
        _ = await h.cache.current(ORG)
        _ = await h.cache.current(OTHER_ORG)
        h.backstop.put(ORG, 12, revoked=[OTHER_BINDING])
        h.cache.on_change(change_event(ORG, 12, previous=11))
        layer = await h.cache.current(ORG)
        assert (layer.revision, layer.revoked_bindings) == (12, frozenset({OTHER_BINDING}))
        assert (await h.cache.current(OTHER_ORG)).revision == 3
        assert h.loads() == 3

    async def test_ll4_an_agents_layer_narrows_its_runs_on_top_of_the_orgs(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10, _writes("ask"), _writes("deny", "a7"), _writes("allow", "a8"))
        layer = await h.cache.current(ORG)
        assert (_write_level(layer), _write_level(layer, "a7"), _write_level(layer, "a8"), _write_level(layer, "a9")) == (
            "ask", "deny", "ask", "ask",
        )

    async def test_ll5_at_most_the_bound_of_orgs_is_held(self, live_harness: LiveHarness) -> None:
        h = live_harness
        for org in ("o1", "o2", "o3"):
            h.backstop.put(org, 1)
            _ = await h.cache.current(org)
        assert h.loads() == 3
        _ = await h.cache.current("o3")
        assert h.loads() == 3
        _ = await h.cache.current("o1")
        assert h.loads() == 4

    async def test_cpd6_41_a_fresh_layer_is_read_without_the_route_and_reloaded_after_its_time_to_live(
        self, live_harness: LiveHarness
    ) -> None:
        h = live_harness
        h.backstop.put(ORG, 10)
        _ = await h.cache.current(ORG)
        _ = h.clock.advance(29.0)
        for _ in range(50):
            _ = await h.cache.current(ORG)
        assert h.loads() == 1
        h.backstop.put(ORG, 11, _writes("deny"))
        _ = h.clock.advance(1.0)
        layer = await h.cache.current(ORG)
        assert (layer.revision, _write_level(layer), h.loads()) == (11, "deny", 2)

    async def test_cpd6_41_concurrent_readers_share_one_reload(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10)
        layers = await asyncio.gather(*(h.cache.current(ORG) for _ in range(8)))
        assert {layer.revision for layer in layers} == {10}
        assert h.loads() == 1

    async def test_ntf09_an_unreachable_route_keeps_the_layer_then_marks_it_stale(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.put(ORG, 10, _writes("ask"))
        _ = await h.cache.current(ORG)
        h.backstop.unreachable = True
        h.cache.on_change(change_event(ORG, 11))
        _ = h.clock.advance(29.0)
        held = await h.cache.current(ORG)
        assert (held.revision, _write_level(held), held.stale) == (10, "ask", False)
        _ = h.clock.advance(1.0)
        stale = await h.cache.current(ORG)
        assert (stale.revision, _write_level(stale), stale.stale) == (10, "ask", True)
        h.backstop.unreachable = False
        h.backstop.put(ORG, 11, _writes("deny"))
        _ = h.clock.advance(5.0)
        back = await h.cache.current(ORG)
        assert (back.revision, _write_level(back), back.stale) == (11, "deny", False)

    async def test_ntf09_an_org_never_loaded_is_stale_while_the_route_is_unreachable(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.unreachable = True
        layer = await h.cache.current(ORG)
        assert (layer.stale, layer.org.entries, layer.revoked_bindings) == (True, {}, frozenset())

    async def test_ntf09_an_outage_does_not_put_a_request_on_every_read(self, live_harness: LiveHarness) -> None:
        h = live_harness
        h.backstop.unreachable = True
        for _ in range(20):
            assert (await h.cache.current(ORG)).stale
        assert h.loads() == 1


__all__ = [
    "ChangeNotifierContract",
    "CredentialBrokerContract",
    "ItemRuntimeContract",
    "ItemRuntimeRegistryContract",
    "LiveLayerCacheContract",
]
