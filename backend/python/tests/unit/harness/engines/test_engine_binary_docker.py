"""EH-13 (EH1, ENG-36): each real engine adapter, in a real broker sandbox (runc), refuses an image retagged with a
binary that is not the allow-listed one, even one that reports the pinned version, before that binary runs.

The test image wraps the real binary in a script that leaves a marker and then runs it, so the version check
alone would pass; only the hash tells them apart.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.engines.errors import EngineBinaryMismatch
from harness_core.ids import RandomIds
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run
from tests.support.docker_images import build_test_image
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.sandbox_broker.conftest import cleanup
from tests.unit.harness_engines.test_engine_binary import BY_KIND, Engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

LEASE = LeaseRef("worker-eh1", 1)
MARKER = "/workspace/.pxh-eh1-ran"
VERSION_ENV = {"HOME": "/tmp", "DISABLE_AUTOUPDATER": "1"}


def _base_image(kind: str) -> str:
    release = lane_release(kind)
    return release.variants.get("engine", release.image)


def _retagged_dockerfile(base: str, binary: str) -> str:
    wrapper = f'#!/bin/sh\\necho ran > {MARKER}\\nexec {binary}.real "$@"\\n'
    return (
        f"FROM {base}\n"
        "USER 0:0\n"
        f"RUN mv {binary} {binary}.real && printf '{wrapper}' > {binary} && chmod 0555 {binary}\n"
        "USER 1000:1000\n"
    )


@pytest.fixture(scope="module")
def provider() -> Iterator[DockerClientProvider]:
    docker_provider = DockerClientProvider(max_workers=8)
    try:
        docker_provider.client.ping()
    except Exception as exc:
        pytest.skip(f"needs a Docker daemon: {type(exc).__name__}")
    yield docker_provider
    docker_provider.close()


@pytest.fixture(params=sorted(BY_KIND), scope="module")
def retagged(request: pytest.FixtureRequest, provider: DockerClientProvider) -> Iterator[tuple[Engine, str]]:
    engine = BY_KIND[request.param]
    base = _base_image(engine.kind)
    try:
        provider.client.images.get(base)
    except Exception:
        pytest.skip(f"needs {base} (deployment/sandbox-images/{engine.image_dir}/build.sh)")
    binary = engine.launch().argv[0]
    tag = f"pxh-eh1-{engine.image_dir}:retagged-{uuid.uuid4().hex[:8]}"
    dockerfile = _retagged_dockerfile(base, binary)
    build_test_image(provider.client, tag, dockerfile)
    try:
        yield engine, tag
    finally:
        provider.client.images.remove(tag, force=True)


@pytest.fixture
async def broker(provider: DockerClientProvider, retagged: tuple[Engine, str]) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.eh1test.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-eh1-" + namespace.rsplit(".", 1)[1]
    created = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({retagged[1]}),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)


async def _sandbox(broker: DockerSandboxBroker, image: str) -> DockerSandboxHandle:
    spec = SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=64),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "EH1"},
    )
    return await broker.acquire(spec, lease=LEASE, idempotency_key=f"eh1-{uuid.uuid4().hex}")


async def _marked(handle: DockerSandboxHandle) -> bool:
    _, _, code = await run(await handle.exec(["cat", MARKER], env={}), timeout_s=60)
    return code == 0


async def test_eh13_the_retagged_binary_reports_the_pinned_version(
    broker: DockerSandboxBroker, retagged: tuple[Engine, str]
) -> None:
    """The control: run by hand, the impostor passes for the pinned version and leaves its marker."""
    engine, image = retagged
    handle = await _sandbox(broker, image)
    try:
        out, err, code = await run(await handle.exec([engine.launch().argv[0], "--version"], env=VERSION_ENV), timeout_s=120)
        assert code == 0, err.decode("utf-8", "replace")
        assert engine.pin.version in out.decode("utf-8", "replace")
        assert await _marked(handle)
    finally:
        await broker.release(handle)


async def test_eh13_the_adapter_refuses_it_before_it_runs(broker: DockerSandboxBroker, retagged: tuple[Engine, str]) -> None:
    engine, image = retagged
    handle = await _sandbox(broker, image)
    try:
        with pytest.raises(EngineBinaryMismatch) as refused:
            await engine.adapter().open(handle, engine.launch(), None)
        assert refused.value.reason_code == "binary_mismatch"
        assert engine.pin.binary_digest in str(refused.value)
        assert not await _marked(handle), "the retagged binary ran"
    finally:
        await broker.release(handle)
