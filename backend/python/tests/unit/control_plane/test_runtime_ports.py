"""CP-SKEL: the Python runtime ports' contracts (IR, CBR, CN, LL) on their in-memory adapters, and the revision rule
(RV) the Node twin shares."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.control_plane.runtime.revisions import change_step
from app.control_plane.testing.contracts import (
    BINDING,
    MATERIAL,
    PURPOSE,
    ChangeNotifierContract,
    CredentialBrokerContract,
    ItemRuntimeContract,
    ItemRuntimeRegistryContract,
    LiveLayerCacheContract,
    pinned_binding,
)
from app.control_plane.testing.memory import (
    InMemoryChangeNotifier,
    InMemoryCredentialBroker,
    InMemoryItemRuntime,
    InMemoryItemRuntimeRegistry,
    InMemoryLiveLayerCache,
)
from harness_core.credentials.errors import CredentialExpired
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue



def _broker(clock: ManualClock) -> InMemoryCredentialBroker:
    counter = itertools.count(1)
    return InMemoryCredentialBroker(clock, ids=lambda: f"{next(counter):08d}")


@dataclass
class _RuntimeHarness:
    runtime: InMemoryItemRuntime = field(default_factory=lambda: InMemoryItemRuntime("mcp"))
    credentials: InMemoryCredentialBroker = field(default_factory=lambda: _broker(ManualClock()))

    def open_sessions(self) -> int:
        return self.runtime.open_sessions


class TestInMemoryItemRuntimeContract(ItemRuntimeContract):
    @pytest.fixture
    def item_runtime(self) -> _RuntimeHarness:
        return _RuntimeHarness()


class TestInMemoryItemRuntimeRegistryContract(ItemRuntimeRegistryContract):
    @pytest.fixture
    def registry_factory(self) -> Callable[[], InMemoryItemRuntimeRegistry]:
        return InMemoryItemRuntimeRegistry


@dataclass
class _BrokerHarness:
    clock: ManualClock = field(default_factory=ManualClock)

    def __post_init__(self) -> None:
        self.broker = _broker(self.clock)

    def store(self, binding: str, material: JsonValue) -> str:
        return self.broker.store(binding, material)

    def tombstone(self, binding: str) -> None:
        self.broker.tombstone(binding)


class TestInMemoryCredentialBrokerContract(CredentialBrokerContract):
    @pytest.fixture
    def broker_harness(self) -> _BrokerHarness:
        return _BrokerHarness()


async def test_cbr_a_lease_expires_with_its_ttl() -> None:
    h = _BrokerHarness()
    lease = await h.broker.lease(pinned_binding(version_digest=h.store(BINDING, MATERIAL)), PURPOSE, 60)
    _ = h.clock.advance(60)
    with pytest.raises(CredentialExpired):
        _ = await h.broker.resolve(lease, PURPOSE)


@dataclass
class _NotifierHarness:
    notifier: InMemoryChangeNotifier = field(default_factory=InMemoryChangeNotifier)

    async def settled(self) -> None:
        return None


class TestInMemoryChangeNotifierContract(ChangeNotifierContract):
    @pytest.fixture
    def notifier_harness(self) -> _NotifierHarness:
        return _NotifierHarness()


@dataclass
class _LiveHarness:
    clock: ManualClock = field(default_factory=ManualClock)

    def __post_init__(self) -> None:
        self.cache = InMemoryLiveLayerCache(self.clock, bound=2)
        self.backstop = self.cache.source

    def loads(self) -> int:
        return len(self.backstop.calls)


class TestInMemoryLiveLayerCacheContract(LiveLayerCacheContract):
    @pytest.fixture
    def live_harness(self) -> _LiveHarness:
        return _LiveHarness()


@pytest.mark.parametrize(
    ("as_of", "revision", "previous", "step"),
    [
        (5, 5, 4, "duplicate"),
        (5, 3, 2, "duplicate"),
        (5, 6, 5, "next"),
        (5, 7, 6, "gap"),
        (5, 7, 4, "gap"),
    ],
    ids=["rv1-held", "rv1-older", "rv2-next", "rv3-missed", "rv3-behind"],
)
def test_rv_the_revision_rule(as_of: int, revision: int, previous: int, step: str) -> None:
    assert change_step(as_of, revision=revision, previous=previous) == step
