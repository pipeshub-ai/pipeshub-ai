"""H1 EI-1 (E9 availability): the Docker broker reports the pinned engines it cannot run, before any run asks.

Unit cases drive ``EngineImageAvailability`` over a fake provider; the ``docker`` cases ask a real daemon about
an image it has and one nobody built, and read the broker's health.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.engine_images import EngineImageAvailability
from app.sandbox_broker_main import engine_images
from harness_core.ids import RandomIds
from harness_core.sandbox.types import (
    ENGINES_UNAVAILABLE,
    ENGINES_UNTRUSTED,
    engines_unavailable,
)
from harness_core.testing.clock import ManualClock
from harness_engines.release import release_allowlist
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.sandbox.docker_client import DockerClientProvider

LOCAL = "pxh-engine-local:1"
PULLABLE = "registry.example/pxh-engine-pullable:1"
NOWHERE = "pxh-engine-nowhere:1"
ENGINES = {LOCAL: "opencode@1", PULLABLE: "codex@1", NOWHERE: "claude_code@1"}


@dataclass
class _Images:
    registry: set[str]
    stall: bool = False
    looked_up: list[str] = field(default_factory=list[str])
    pulled: list[str] = field(default_factory=list[str])

    def get_registry_data(self, image: str) -> object:
        self.looked_up.append(image)
        if image not in self.registry:
            raise RuntimeError("manifest unknown")
        return object()

    def pull(self, image: str) -> None:
        self.pulled.append(image)


@dataclass
class FakeProvider:
    local: set[str] = field(default_factory=lambda: {LOCAL})
    images: _Images = field(default_factory=lambda: _Images(registry={PULLABLE}))

    @property
    def client(self) -> FakeProvider:
        return self

    async def ensure_image(self, image: str) -> bool:
        return image in self.local

    async def run_blocking(self, fn: Callable[..., object], *args: object) -> object:
        if self.images.stall:
            await asyncio.sleep(10)
        return fn(*args)


def _availability(provider: FakeProvider, clock: ManualClock, **kwargs: float) -> EngineImageAvailability:
    return EngineImageAvailability(provider, ENGINES, clock, **kwargs)  # type: ignore[arg-type]


async def test_ei1_only_an_engine_neither_here_nor_in_its_registry_is_unavailable_and_nothing_is_pulled() -> None:
    provider = FakeProvider()
    assert await _availability(provider, ManualClock()).unavailable() == ("claude_code@1",)
    assert provider.images.pulled == []
    assert sorted(provider.images.looked_up) == sorted([PULLABLE, NOWHERE])


async def test_ei1_answers_are_remembered_and_a_missing_image_is_looked_at_again_later() -> None:
    provider, clock = FakeProvider(), ManualClock()
    availability = _availability(provider, clock, recheck_s=60.0)
    _ = await availability.unavailable()
    provider.images.looked_up.clear()
    assert await availability.unavailable() == ("claude_code@1",)
    assert provider.images.looked_up == [], "a present image and a recent miss are not looked up again"
    provider.local.add(NOWHERE)  # the admin built it
    _ = clock.advance(61)
    assert await availability.unavailable() == ()


async def test_ei1_a_lookup_that_does_not_answer_decides_nothing() -> None:
    provider = FakeProvider(images=_Images(registry=set(), stall=True))
    availability = _availability(provider, ManualClock(), registry_timeout_s=0.01)
    assert await availability.unavailable() == ()


def test_ei1_the_broker_names_each_pinned_engine_by_its_run_image() -> None:
    images = engine_images()
    assert set(images) == {r.image for r in release_allowlist()}
    for release in release_allowlist():
        assert f"{release.version.kind}@{release.version.version}" in images[release.image]
    assert {r.version.ref for r in release_allowlist()} == {ref for refs in images.values() for ref in refs}


def test_acp_an_acp_engine_shares_the_native_engines_image() -> None:
    """ACP-driver: opencode's ACP profile runs in the native opencode run image; both are watched there."""
    opencode = next(r for r in release_allowlist() if r.version.kind == "opencode")
    assert engine_images()[opencode.image] == ("opencode@1.18.34", "acp-opencode@1.18.34")


async def test_acp_engines_sharing_a_missing_image_are_all_unavailable() -> None:
    provider = FakeProvider(images=_Images(registry=set()))
    availability = EngineImageAvailability(
        provider, {"pxh-gone:1": ("opencode@1", "acp-opencode@1")}, ManualClock()  # type: ignore[arg-type]
    )
    assert await availability.unavailable() == ("acp-opencode@1", "opencode@1")


async def test_acp_an_uninstalled_acp_engine_is_always_unavailable() -> None:
    provider = FakeProvider(local={"pxh-here:1"}, images=_Images(registry=set()))
    availability = EngineImageAvailability(
        provider,
        {"pxh-here:1": ("opencode@1", "acp-opencode@1")},  # type: ignore[arg-type]
        ManualClock(),
        uninstalled=("acp-opencode@1",),
    )
    assert await availability.unavailable() == ("acp-opencode@1",)


@pytest.mark.docker
async def test_ei1_a_real_daemon_reports_the_engine_nobody_built(provider: DockerClientProvider) -> None:
    missing = f"pxh-h1-never-built-{uuid.uuid4().hex[:8]}:0"
    availability = EngineImageAvailability(
        provider, {DOCKER_TEST_IMAGE: "opencode@0", missing: "claude_code@0"}, ManualClock()
    )
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=f"pxh-h1-{uuid.uuid4().hex[:6]}",
        engine_images=availability,
    )
    health = await broker.health()
    assert health.detail[ENGINES_UNAVAILABLE] == "claude_code@0"
    assert engines_unavailable(health) == frozenset({"claude_code@0"})
    images = await provider.run_blocking(provider.client.images.list, name=missing.split(":")[0])
    assert images == [], "the lookup pulled nothing"


@pytest.mark.docker
async def test_r32_the_health_names_an_engine_whose_profile_is_untrusted_though_its_image_is_here(
    provider: DockerClientProvider,
) -> None:
    """R32-TRUST (ADR-SEC-01 D7): Node words it "not signed for this deployment", never "not installed"."""
    availability = EngineImageAvailability(
        provider,
        {DOCKER_TEST_IMAGE: ("opencode@0", "acp-opencode@0")},
        ManualClock(),
        uninstalled=("acp-opencode@0",),
        untrusted=("acp-opencode@0",),
    )
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=f"pxh-r32-{uuid.uuid4().hex[:6]}",
        engine_images=availability,
    )
    health = await broker.health()
    assert health.detail[ENGINES_UNAVAILABLE] == "acp-opencode@0"
    assert health.detail[ENGINES_UNTRUSTED] == "acp-opencode@0"


@pytest.mark.docker
async def test_ei1_a_broker_without_engines_to_watch_reports_nothing(provider: DockerClientProvider) -> None:
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=ManualClock(), ids=RandomIds(), label_namespace=f"pxh-h1-{uuid.uuid4().hex[:6]}"
    )
    assert engines_unavailable(await broker.health()) is None


async def test_pp1_c5_01_the_broker_pre_pulls_what_its_registry_has_and_this_daemon_lacks() -> None:
    """PP-1 (C5.01: harness images pre-pulled, never pulled at session time): at start the broker pulls each pinned
    engine image it lacks and its registry has; one it has, or nobody has, is not pulled. Pulled, it is present."""
    provider = FakeProvider()

    async def pull(image: str) -> None:
        provider.images.pull(image)
        provider.local.add(image)

    provider.pull_image = pull  # type: ignore[attr-defined]
    availability = _availability(provider, ManualClock())
    assert await availability.prepull() == (PULLABLE,)
    assert provider.images.pulled == [PULLABLE]
    provider.images.looked_up.clear()
    assert await availability.unavailable() == ("claude_code@1",)
    assert PULLABLE not in provider.images.looked_up


async def test_pp1_a_failed_pre_pull_leaves_acquisition_to_decide() -> None:
    provider = FakeProvider()

    async def refused(image: str) -> None:
        raise RuntimeError(f"pull access denied for {image}")

    provider.pull_image = refused  # type: ignore[attr-defined]
    assert await _availability(provider, ManualClock()).prepull() == ()


async def test_pp2_an_acquire_during_the_pre_pull_joins_it_instead_of_pulling_again() -> None:
    """PP-2: one pull per image at a time: the session that arrives while the start-up pull runs waits for that
    pull; a later one finds the image cached."""
    from app.harness.sandbox.docker_client import DockerClientProvider

    started, release = asyncio.Event(), asyncio.Event()
    pulls: list[str] = []
    loop = asyncio.get_running_loop()

    class Images:
        def pull(self, image: str) -> None:
            pulls.append(image)
            loop.call_soon_threadsafe(started.set)
            asyncio.run_coroutine_threadsafe(release.wait(), loop).result(5)

    provider = DockerClientProvider(max_workers=2)
    provider._client = type("Client", (), {"images": Images()})()  # pyright: ignore[reportPrivateUsage]
    try:
        first = asyncio.ensure_future(provider.pull_image(PULLABLE))
        await asyncio.wait_for(started.wait(), 5)
        second = asyncio.ensure_future(provider.pull_image(PULLABLE))
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert pulls == [PULLABLE]
        assert await provider.ensure_image(PULLABLE)
    finally:
        provider._client = None  # pyright: ignore[reportPrivateUsage]
        provider.close()
