"""EH2-18..EH2-21 (EH2, SBX-36, ENG-38): on real images (runc), nothing in an engine sandbox can change the engine
binary, so the broker's one check per image id stands for every session; a tag moved to other bytes is refused.

The write attempts run as the sandbox user, the way an engine's own tools would.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.engines.binary import PinnedBinary, verify_binary
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import (
    ImageNotAllowed,
    InvalidSandboxRequest,
    SandboxError,
)
from harness_core.sandbox.streams import iter_bytes
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run, tar_of
from harness_engines.release import BINARY_PATHS, release_binary_pins
from tests.support.docker_images import build_test_image
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_release,
    lane_releases,
)
from tests.unit.harness.engines.test_engine_binary_docker import (
    _retagged_dockerfile,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.sandbox_broker.conftest import cleanup
from tests.unit.harness_engines.test_engine_binary import BY_KIND

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from app.harness.sandbox.docker_broker import DockerSandboxHandle
    from harness_core.sandbox.ports import ProcessHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

LEASE = LeaseRef("worker-eh2", 1)
OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "EH2"}
_LOG = HarnessLog(logging.getLogger("tests.eh2.docker"))
# Each tries to change the file at $1 (the binary); every one must fail.
WRITES = {
    "overwrite": 'printf x > "$1"',
    "truncate": ': > "$1"',
    "append": 'printf x >> "$1"',
    "remove": 'rm -f "$1"',
    "rename": 'mv -f "$1" "$1.old"',
    "replace-by-rename": 'cp /bin/sh /tmp/impostor && mv -f /tmp/impostor "$1"',
    "copy-over": 'cp -f /bin/sh "$1"',
    "chmod": 'chmod u+w "$1"',
    "hard-link-over": 'ln -f /bin/sh "$1"',
    "symlink-over": 'ln -sf /bin/sh "$1"',
    "remount": "mount -o remount,rw /",
}


def _spec(image: str, *, labels: Mapping[str, str] | None = None) -> SandboxSpec:
    """An engine turn's mounts (``engine_sandbox_spec``): workspace, state and the read-only skills folder."""
    return SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=64),
        mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount("/skills")),
        labels={**OWNER, **(labels or {})},
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


@pytest.fixture
def namespace(provider: DockerClientProvider) -> Iterator[str]:
    name = f"io.pipeshub.eh2test.{uuid.uuid4().hex[:8]}"
    yield name
    cleanup(provider, name, _prefix(name))


def _prefix(namespace: str) -> str:
    return "pxh-eh2-" + namespace.rsplit(".", 1)[1]


def _present(provider: DockerClientProvider, image: str) -> None:
    try:
        provider.client.images.get(image)
    except Exception:
        pytest.skip(f"needs {image} (deployment/sandbox-images/*/build.sh)")


async def _broker(
    provider: DockerClientProvider, namespace: str, images: Sequence[str], pins: Mapping[str, Sequence[PinnedBinary]]
) -> DockerSandboxBroker:
    return await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset(images),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=namespace,
        volume_prefix=_prefix(namespace),
        binary_pins=pins,
    )


async def _exit(process: ProcessHandle) -> int:
    _, _, code = await run(process, timeout_s=120)
    return code


@pytest.mark.parametrize("kind", sorted(BY_KIND))
async def test_eh2_18_nothing_in_an_engine_sandbox_can_change_its_binary(
    provider: DockerClientProvider, namespace: str, kind: str
) -> None:
    release = lane_release(kind)
    _present(provider, release.image)
    path = BINARY_PATHS[release.version.kind]
    broker = await _broker(provider, namespace, [release.image], release_binary_pins(lane_releases()))
    handle = await broker.acquire(_spec(release.image), lease=LEASE, idempotency_key=f"eh2-{uuid.uuid4().hex}")
    try:
        assert dict(handle.verified_binaries) == {path: release.version.binary_digest}
        changed = {
            name: code
            for name, script in WRITES.items()
            if (code := await _exit(await handle.exec(["/bin/sh", "-c", script, "write", path], env={}))) == 0
        }
        assert changed == {}, f"{kind}: these writes succeeded: {sorted(changed)}"
        with pytest.raises(SandboxError):
            await handle.put_tar(path.rsplit("/", 1)[0], iter_bytes(tar_of({path.rsplit("/", 1)[1]: b"#!/bin/sh\n"})))
        with pytest.raises(InvalidSandboxRequest):
            await handle.seed_tar(path.rsplit("/", 1)[0], iter_bytes(tar_of({path.rsplit("/", 1)[1]: b"#!/bin/sh\n"})))
        out, _, code = await run(await handle.exec(["sha256sum", path], env={}), timeout_s=120)
        assert code == 0
        assert f"sha256:{out.decode().split()[0]}" == release.version.binary_digest
    finally:
        await broker.release(handle)


async def test_eh2_18_a_mount_over_the_binary_is_not_vouched_for(provider: DockerClientProvider, namespace: str) -> None:
    """The control for the proof: where a spec does let the sandbox replace the binary, the broker vouches for nothing."""
    release = lane_release("codex")
    _present(provider, release.image)
    path = BINARY_PATHS[release.version.kind]
    broker = await _broker(provider, namespace, [release.image], release_binary_pins(lane_releases()))
    base = _spec(release.image)
    spec = SandboxSpec(base.image_digest, base.mode, base.resources, base.egress, (*base.mounts, MountSpec(path.rsplit("/", 1)[0])), base.labels)
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"eh2-{uuid.uuid4().hex}")
    try:
        assert dict(handle.verified_binaries) == {}
        assert await _exit(await handle.exec(["/bin/sh", "-c", WRITES["copy-over"], "write", path], env={})) == 0
    finally:
        await broker.release(handle)


class _Recording:
    """The real handle, recording what is run in it."""

    def __init__(self, inner: DockerSandboxHandle) -> None:
        self.inner = inner
        self.execs: list[list[str]] = []

    @property
    def id(self) -> str:
        return self.inner.id

    @property
    def mode(self) -> RuntimeMode:
        return self.inner.mode

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return self.inner.verified_binaries

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> ProcessHandle:
        self.execs.append(list(argv))
        return await self.inner.exec(argv, env=env, stdin=stdin)


@pytest.mark.parametrize("kind", sorted(BY_KIND))
async def test_eh2_19_a_session_on_a_verified_image_runs_no_hash(
    provider: DockerClientProvider, namespace: str, kind: str
) -> None:
    release = lane_release(kind)
    _present(provider, release.image)
    path = BINARY_PATHS[release.version.kind]
    broker = await _broker(provider, namespace, [release.image], release_binary_pins(lane_releases()))
    handle = await broker.acquire(_spec(release.image), lease=LEASE, idempotency_key=f"eh2-{uuid.uuid4().hex}")
    try:
        recording = _Recording(handle)
        await verify_binary(recording, path, release.version, _LOG)  # pyright: ignore[reportArgumentType]
        assert recording.execs == []
    finally:
        await broker.release(handle)


@pytest.fixture
def moved_tag(provider: DockerClientProvider, request: pytest.FixtureRequest) -> Iterator[tuple[str, str, str]]:
    """(engine kind, a tag on the release image, EH1's wrapper image to move it to)."""
    kind: str = request.param
    engine = BY_KIND[kind]
    release = lane_release(kind)
    base = release.variants.get("engine", release.image)
    _present(provider, base)
    suffix = uuid.uuid4().hex[:8]
    repository = f"pxh-eh2-{engine.image_dir}"
    tag, wrapper = f"{repository}:moved-{suffix}", f"{repository}:wrapper-{suffix}"
    build_test_image(provider.client, wrapper, _retagged_dockerfile(base, BINARY_PATHS[release.version.kind]))
    assert provider.client.images.get(base).tag(repository, f"moved-{suffix}")
    try:
        yield kind, tag, wrapper
    finally:
        for image in (tag, wrapper):
            provider.client.images.remove(image, force=True)


@pytest.mark.parametrize("moved_tag", sorted(BY_KIND), indirect=True)
async def test_eh2_20_a_tag_moved_to_other_bytes_is_refused(
    provider: DockerClientProvider, namespace: str, moved_tag: tuple[str, str, str]
) -> None:
    kind, tag, wrapper = moved_tag
    release = lane_release(kind)
    path = BINARY_PATHS[release.version.kind]
    broker = await _broker(provider, namespace, [tag], {tag: (PinnedBinary(release.version, path),)})
    first = await broker.acquire(_spec(tag), lease=LEASE, idempotency_key=f"eh2-{uuid.uuid4().hex}")
    try:
        assert dict(first.verified_binaries) == {path: release.version.binary_digest}
        repository, _, moved = tag.partition(":")
        assert provider.client.images.get(wrapper).tag(repository, moved, force=True)
        for _ in range(2):
            with pytest.raises(ImageNotAllowed) as refused:
                await broker.acquire(_spec(tag), lease=LEASE, idempotency_key=f"eh2-{uuid.uuid4().hex}")
            assert refused.value.reason == "binary_mismatch"
        wrapper_id = provider.client.images.get(wrapper).id
        left = provider.client.api.containers(all=True, filters={"label": [f"{namespace}.managed=1"]})
        # The first sandbox and its seeder (PF-01: made with a sandbox that has read-only scratch mounts).
        assert sorted(c["Labels"].get(f"{namespace}.id") or c["Labels"].get(f"{namespace}.seeds") for c in left) == [
            first.id, first.id
        ], "a container of the moved tag was left behind"
        assert first.container_id in [c["Id"] for c in left]
        assert all(c["ImageID"] != wrapper_id for c in left)
    finally:
        await broker.release(first)
