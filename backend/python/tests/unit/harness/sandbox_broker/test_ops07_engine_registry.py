"""OPS-07 (ADR-OPS-01 D9): ``HARNESS_ENGINE_IMAGE_REGISTRY`` prefixes every engine image the worker asks for and the
broker allows.

Unset, every name is the allowlist's bare one, as before. Set, the worker's controller, the broker's allowed set, its
availability check and its digest and binary pins all use ``{registry}/{bare name}``, so the bare name is no longer
allowed; ``EngineRelease.image`` keeps the bare name the ACP profiles compare with.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.engines.worker import controller_config
from app.harness.sandbox.docker_policy import HardeningPolicy
from app.harness.sandbox.engine_images import EngineImageAvailability
from app.sandbox_broker_main import allowed_images, engine_images
from harness_core.testing.clock import ManualClock
from harness_engines.release import (
    ENGINE_IMAGE_REGISTRY_ENV,
    EngineImageRegistryInvalid,
    engine_image_registry,
    installed_acp,
    release_allowlist,
    release_binary_pins,
    release_image_pins,
    release_image_resources,
    release_images,
    run_image,
)

if TYPE_CHECKING:
    from collections.abc import Callable

MIRROR = "registry.internal:5000/pipeshub"


@pytest.fixture
def mirrored(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(ENGINE_IMAGE_REGISTRY_ENV, f" {MIRROR}/ ")
    return MIRROR


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENGINE_IMAGE_REGISTRY_ENV, raising=False)


def bare_images() -> set[str]:
    return {r.image for r in release_allowlist()}


@pytest.mark.usefixtures("unset")
def test_ops_07_unset_every_engine_image_is_the_allowlists_bare_name() -> None:
    releases = release_allowlist()
    assert engine_image_registry() is None
    assert release_images() == bare_images()
    assert set(engine_images()) == bare_images()
    assert {r.image for r in releases if r.image_digest} == set(release_image_pins())
    assert {r.image for r in releases if r.cpus is not None or r.min_memory_mb is not None} == set(release_image_resources())
    assert set(controller_config(releases).images.values()) == bare_images()
    assert bare_images() <= allowed_images()


@pytest.mark.parametrize("blank", ["", "  ", "/"])
def test_ops_07_a_blank_registry_is_unset(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv(ENGINE_IMAGE_REGISTRY_ENV, blank)
    assert engine_image_registry() is None
    assert run_image("pxh-codex-tools:1") == "pxh-codex-tools:1"


@pytest.mark.parametrize(
    "value",
    ["https://registry.internal", "/pipeshub", "registry.internal//pipeshub", "registry.internal/pipeshub:1",
     "registry.internal/p@sha256", "registry internal"],
)
def test_ops_07_a_registry_that_is_not_host_and_path_stops_the_process(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ENGINE_IMAGE_REGISTRY_ENV, value)
    with pytest.raises(EngineImageRegistryInvalid):
        _ = release_images()


@pytest.mark.usefixtures("mirrored")
def test_ops_07_set_the_broker_allows_only_the_prefixed_names() -> None:
    prefixed = {f"{MIRROR}/{image}" for image in bare_images()}
    assert release_images() == prefixed
    allowed = allowed_images()
    assert prefixed <= allowed
    assert not bare_images() & allowed
    policy = HardeningPolicy(user="1000:1000", allowed_networks=frozenset(), volume_allowed=lambda _: False,
                             allowed_images=allowed)
    for image in bare_images():
        assert policy.image_allowed(f"{MIRROR}/{image}")
        assert not policy.image_allowed(image)


@pytest.mark.usefixtures("mirrored")
def test_ops_07_set_engine_images_and_the_worker_ask_for_the_same_prefixed_names() -> None:
    releases = release_allowlist()
    images = engine_images()
    assert set(images) == {f"{MIRROR}/{r.image}" for r in releases}
    assert {ref for refs in images.values() for ref in refs} == {r.version.ref for r in releases}
    config = controller_config(releases)
    assert config.images == {r.version: f"{MIRROR}/{r.image}" for r in releases}
    assert set(config.image_resources) <= set(images)
    # The allowlist itself keeps the bare name: an installed ACP profile still matches it.
    assert {r.image for r in releases} == bare_images()
    assert all(profile.image in bare_images() for profile in installed_acp().values())


def test_ops_07_set_digest_and_binary_pins_are_keyed_by_the_prefixed_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENGINE_IMAGE_REGISTRY_ENV, raising=False)
    binaries, digests = release_binary_pins(), release_image_pins()
    assert binaries and digests is not None
    monkeypatch.setenv(ENGINE_IMAGE_REGISTRY_ENV, MIRROR)
    assert release_binary_pins() == {f"{MIRROR}/{image}": pins for image, pins in binaries.items()}
    assert release_image_pins() == {f"{MIRROR}/{image}": digest for image, digest in digests.items()}
    assert run_image("pxh-x:1") == f"{MIRROR}/pxh-x:1"


@dataclass
class _Registry:
    looked_up: list[str] = field(default_factory=list[str])
    pulled: list[str] = field(default_factory=list[str])

    def get_registry_data(self, image: str) -> object:
        self.looked_up.append(image)
        raise RuntimeError("no route to any registry")

    def pull(self, image: str) -> None:
        self.pulled.append(image)


@dataclass
class _OfflineDaemon:
    loaded: set[str]
    images: _Registry = field(default_factory=_Registry)

    @property
    def client(self) -> _OfflineDaemon:
        return self

    async def ensure_image(self, image: str) -> bool:
        return image in self.loaded

    async def run_blocking(self, fn: Callable[..., object], *args: object) -> object:
        return fn(*args)


@pytest.mark.usefixtures("mirrored")
def test_ops_07_an_engine_loaded_under_its_mirror_name_is_available_offline_and_a_bare_copy_is_not() -> None:
    releases = release_allowlist()
    codex = next(r.image for r in releases if r.version.kind == "codex")
    daemon = _OfflineDaemon(loaded={f"{MIRROR}/{r.image}" for r in releases if r.image != codex} | {codex})
    availability = EngineImageAvailability(daemon, engine_images(), ManualClock())  # type: ignore[arg-type]
    unavailable = asyncio.run(availability.unavailable())
    assert unavailable and all(ref.startswith("codex@") for ref in unavailable), unavailable
    assert daemon.images.looked_up == [f"{MIRROR}/{codex}"]
