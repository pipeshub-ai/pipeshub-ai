"""EH-9 (EH1, SBX-31): the real Docker broker (runc) refuses an image carrying the candidate label, whatever its
tag, and creates nothing; only a lane gating a candidate runs it."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import ImageNotAllowed
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_engines.release import CANDIDATE_LABEL
from tests.support.docker_images import build_test_image
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

from .conftest import AGENT_LABEL

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_client import DockerClientProvider

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("worker-eh1", 1)


@pytest.fixture
def candidate_image(provider: DockerClientProvider) -> Iterator[str]:
    """The sandbox test image with the candidate label added: a candidate build under a tag of its own."""
    tag = f"pxh-eh1-candidate:{uuid.uuid4().hex[:8]}"
    dockerfile = f"FROM {DOCKER_TEST_IMAGE}\nLABEL {CANDIDATE_LABEL}=true\n"
    build_test_image(provider.client, tag, dockerfile)
    yield tag
    provider.client.images.remove(tag, force=True)


async def _broker(
    provider: DockerClientProvider, namespace: str, prefix: str, image: str, *, admit: bool
) -> DockerSandboxBroker:
    return await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE, image}),
        label_namespace=namespace,
        volume_prefix=prefix,
        admit_candidate_images=admit,
    )


def _spec(image: str) -> SandboxSpec:
    return SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        mounts=(MountSpec("/work"),),
        labels=dict(AGENT_LABEL),
    )


def _key() -> str:
    return f"eh1-{uuid.uuid4().hex}"


async def test_eh9_a_candidate_build_is_refused_and_nothing_is_created(
    provider: DockerClientProvider, namespace: str, volume_prefix: str, cleanup_namespace: None, candidate_image: str
) -> None:
    broker = await _broker(provider, namespace, volume_prefix, candidate_image, admit=False)
    with pytest.raises(ImageNotAllowed) as refused:
        await broker.acquire(_spec(candidate_image), lease=LEASE, idempotency_key=_key())
    assert refused.value.reason == "candidate_image"
    assert provider.client.api.containers(all=True, filters={"label": [f"{namespace}.managed=1"]}) == []
    plain = await broker.acquire(_spec(DOCKER_TEST_IMAGE), lease=LEASE, idempotency_key=_key())
    await broker.release(plain)


async def test_eh9_the_lane_override_runs_it(
    provider: DockerClientProvider, namespace: str, volume_prefix: str, cleanup_namespace: None, candidate_image: str
) -> None:
    broker = await _broker(provider, namespace, volume_prefix, candidate_image, admit=True)
    handle = await broker.acquire(_spec(candidate_image), lease=LEASE, idempotency_key=_key())
    await broker.release(handle)
