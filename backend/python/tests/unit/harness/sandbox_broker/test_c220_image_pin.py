"""C2.20: images are pinned by digest. The Docker broker reports each engine image's digest in its health (the
digest a run pins at claim), treats an image that is not the release's pinned digest as unavailable, and refuses
a sandbox pinned to one digest that was created from any other image (``image_digest_mismatch``), whatever the tag
names now: a rebuilt image, a warm sandbox of the old one, an adopted container."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.sandbox.engine_images import EngineImageAvailability
from harness_core.sandbox.errors import ImageNotAllowed
from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import ENGINE_IMAGES, LeaseRef, engine_image_digests
from harness_core.testing.clock import ManualClock
from harness_engines.release import release_allowlist, release_image_pins

if TYPE_CHECKING:
    from app.harness.sandbox.docker_client import DockerClientProvider

from .test_binary_cache import (
    BAD_ID,
    ENGINE,
    GOOD_ID,
    LEASE,
    PATH,
    TAG,
    _broker,
    _Provider,
    _reader,
    _spec,
)

REPO_DIGEST = "sha256:" + "9" * 64
REBUILT_ID = "sha256:" + "8" * 64


class _DigestProvider(_Provider):
    """The daemon's answer to ``docker image inspect``: the image id, and repo digests when it was pulled."""

    def __init__(self, image_ids: dict[str, str], repo_digests: dict[str, list[str]] | None = None) -> None:
        super().__init__(image_ids)
        self.repo_digests = repo_digests or {}

    async def image_digests(self, image: str) -> tuple[str, ...]:
        image_id = self.api.image_ids.get(image, image)
        return (image_id, *self.repo_digests.get(image_id, []))


class TestTheBroker:
    async def test_c220_a_sandbox_pinned_to_its_image_s_digest_runs(self) -> None:
        broker = _broker(_DigestProvider({TAG: GOOD_ID}), _reader())
        handle = await broker.acquire(_spec(image_pin=GOOD_ID), lease=LEASE, idempotency_key="a")
        assert dict(handle.verified_binaries) == {PATH: ENGINE.binary_digest}

    async def test_c220_a_registry_s_repo_digest_pins_the_image_it_names(self) -> None:
        provider = _DigestProvider({TAG: GOOD_ID}, repo_digests={GOOD_ID: [REPO_DIGEST]})
        handle = await _broker(provider, _reader()).acquire(_spec(image_pin=REPO_DIGEST), lease=LEASE, idempotency_key="a")
        assert handle.id

    async def test_c220_a_rebuilt_image_under_the_same_tag_is_refused_and_never_started(self) -> None:
        provider = _DigestProvider({TAG: BAD_ID})
        with pytest.raises(ImageNotAllowed) as refused:
            await _broker(provider, _reader()).acquire(_spec(image_pin=GOOD_ID), lease=LEASE, idempotency_key="a")
        assert refused.value.reason == "image_digest_mismatch"
        assert provider.api.started == []
        assert sorted(provider.api.removed) == sorted(provider.api.created)

    async def test_c220_an_image_without_binary_pins_is_still_held_to_its_digest(self) -> None:
        provider = _DigestProvider({"pxh-sandbox:pr1": BAD_ID})
        with pytest.raises(ImageNotAllowed) as refused:
            await _broker(provider, _reader()).acquire(
                _spec(image_digest="pxh-sandbox:pr1", image_pin=GOOD_ID), lease=LEASE, idempotency_key="a"
            )
        assert refused.value.reason == "image_digest_mismatch"

    async def test_c220_a_warm_sandbox_of_the_old_image_is_not_handed_to_a_run_pinned_to_the_new_one(self) -> None:
        provider = _DigestProvider({TAG: GOOD_ID})
        broker = _broker(provider, _reader(), warm_pool=WarmPoolConfig(ceiling=1))
        shape = shape_of(_spec(labels={}))
        assert shape is not None
        warm = await broker.create_warm(shape, {})
        with pytest.raises(ImageNotAllowed) as refused:
            await broker._bind_warm(  # pyright: ignore[reportPrivateUsage]
                warm, _spec(image_pin=BAD_ID), "k", LEASE, proxied=False
            )
        assert refused.value.reason == "image_digest_mismatch"

    async def test_c220_an_adopted_sandbox_of_another_image_is_refused(self) -> None:
        provider = _DigestProvider({TAG: GOOD_ID})
        handle = await _broker(provider, _reader()).acquire(_spec(image_pin=GOOD_ID), lease=LEASE, idempotency_key="k")
        provider.api.created[handle.container_id]["ImageID"] = BAD_ID
        with pytest.raises(ImageNotAllowed):
            await _broker(provider, _reader()).acquire(_spec(image_pin=GOOD_ID), lease=LeaseRef("w", 2), idempotency_key="k")


class _Inspecting:
    """``EngineImageAvailability``'s provider: present images answer with their digests."""

    def __init__(self, digests: dict[str, tuple[str, ...]]) -> None:
        self.digests = digests

    async def ensure_image(self, image: str) -> bool:
        return image in self.digests

    async def image_digests(self, image: str) -> tuple[str, ...]:
        if image not in self.digests:
            raise RuntimeError("No such image")
        return self.digests[image]

    async def run_blocking(self, fn: Any, *args: Any) -> Any:  # noqa: ANN401
        return fn(*args)

    @property
    def client(self) -> Any:  # noqa: ANN401
        return self


class TestTheReport:
    async def test_c220_each_engine_on_the_daemon_is_reported_by_its_digest(self) -> None:
        provider = _Inspecting({"a:1": (GOOD_ID,), "b:1": (BAD_ID, REPO_DIGEST)})
        availability = EngineImageAvailability(provider, {"a:1": "a@1", "b:1": ("b@1", "acp-b@1")}, ManualClock())  # type: ignore[arg-type]
        assert await availability.digests() == {"a@1": GOOD_ID, "b@1": BAD_ID, "acp-b@1": BAD_ID}

    async def test_c220_an_image_that_is_not_the_release_s_pinned_digest_is_unavailable(self) -> None:
        provider = _Inspecting({"a:1": (BAD_ID,), "b:1": (GOOD_ID, REPO_DIGEST)})
        availability = EngineImageAvailability(
            provider,  # type: ignore[arg-type]
            {"a:1": "a@1", "b:1": "b@1"},
            ManualClock(),
            pins={"a:1": GOOD_ID, "b:1": REPO_DIGEST},
        )
        assert await availability.unavailable() == ("a@1",)
        # The release's digest is what a run pins; never the foreign image's.
        assert await availability.digests() == {"b@1": REPO_DIGEST}

    def test_the_health_field_round_trips_to_what_node_and_the_worker_read(self) -> None:
        from harness_core.sandbox.types import BrokerHealth, engine_images_detail

        detail = engine_images_detail({"b@1": BAD_ID, "a@1": GOOD_ID})
        assert detail == f"a@1={GOOD_ID},b@1={BAD_ID}"
        assert engine_image_digests(BrokerHealth(ok=True, modes=frozenset(), detail={ENGINE_IMAGES: detail})) == {
            "a@1": GOOD_ID,
            "b@1": BAD_ID,
        }


class TestTheAllowlist:
    def test_c220_an_allowlist_entry_may_pin_its_image_s_digest(self) -> None:
        raw = json.loads(json.dumps({"engines": [_entry(imageDigest=GOOD_ID)]}))
        (release,) = release_allowlist(json.dumps(raw))
        assert release.image_digest == GOOD_ID
        assert release_image_pins((release,)) == {release.image: GOOD_ID}

    def test_c220_an_allowlist_digest_that_is_not_sha256_is_refused(self) -> None:
        with pytest.raises(ValueError, match="imageDigest"):
            release_allowlist(json.dumps({"engines": [_entry(imageDigest="latest")]}))

    def test_the_shipped_allowlist_pins_no_image_digest_its_images_are_built_on_each_deployment(self) -> None:
        assert release_image_pins() == {}


def _entry(**extra: object) -> dict[str, object]:
    return {
        "kind": "codex",
        "version": "0.160.0",
        "binaryDigest": ENGINE.binary_digest,
        "image": "pxh-codex-tools:0.160.0-ph7",
        **extra,
    }


class TestTheWarmPool:
    def test_c220_a_pinned_spec_needs_a_warm_sandbox_of_its_own_digest(self) -> None:
        from harness_core.sandbox.pool import WarmShape

        unpinned, old, new = shape_of(_spec()), shape_of(_spec(image_pin=GOOD_ID)), shape_of(_spec(image_pin=BAD_ID))
        assert unpinned is not None and old is not None and new is not None
        assert len({unpinned.digest, old.digest, new.digest}) == 3
        assert WarmShape.from_wire(old.to_wire()) == old
        assert old.template().image_pin == GOOD_ID
        # An unpinned shape keeps the wire (and so the digest) it had before C2.20.
        assert "pin" not in json.loads(unpinned.to_wire())


@pytest.mark.docker
async def test_c220_a_real_daemon_reports_the_image_id_and_holds_a_sandbox_to_it(
    provider: DockerClientProvider, namespace: str, volume_prefix: str, cleanup_namespace: None
) -> None:
    """On a real daemon: health names the run image by its id (what a run pins), a sandbox pinned to that id runs,
    and one pinned to any other digest is refused and removed."""
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.ids import RandomIds
    from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

    from .test_docker_broker import _spec as docker_spec

    image_id = (await provider.image_digests(DOCKER_TEST_IMAGE))[0]
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        label_namespace=namespace,
        volume_prefix=volume_prefix,
        engine_images=EngineImageAvailability(provider, {DOCKER_TEST_IMAGE: "opencode@0"}, ManualClock()),
    )
    health = await broker.health()
    assert engine_image_digests(health) == {"opencode@0": image_id}
    handle = await broker.acquire(docker_spec(image_pin=image_id), lease=LEASE, idempotency_key="pinned")
    await broker.release(handle)
    with pytest.raises(ImageNotAllowed) as refused:
        await broker.acquire(docker_spec(image_pin=REBUILT_ID), lease=LEASE, idempotency_key="foreign")
    assert refused.value.reason == "image_digest_mismatch"
