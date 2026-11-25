"""IM-2, BR-1, BR-3 (E0), IM-2c, BR-1c (E8), IM-2cc, BR-1cc (E9), IT-12 (E0b): the engine-only images under the real Docker broker
(runc lane); opencode's tools variant has its own cases (``test_engine_tools_image_docker.py``).

The images have no python3, so ``connect`` must use the image's static relay
(P5.1-f). Each engine's cases skip without a daemon or without its image (``build.sh``).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import SandboxConnectError
from harness_core.sandbox.streams import collect
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import connect_when_listening, run
from harness_engines.release import CANDIDATE_TAG_SUFFIX
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_release,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle
    from harness_engines.release import EngineRelease

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("worker-e0", 1)
ECHO_PORT = 4711


@dataclass(frozen=True, slots=True)
class EngineImage:
    kind: str
    binary: str
    version_argv: tuple[str, ...]
    version_output: str
    notices: tuple[str, ...]
    # Programs that must not be in the image (E8: the standalone binary ships without bubblewrap).
    absent: tuple[str, ...] = ("python3",)
    licence_dir: str = ""
    # A loopback echo server the image can run (busybox ``nc`` on Alpine; Debian's perl-base for Claude Code).
    echo_server: tuple[str, ...] = ("nc", "-lk", "-s", "127.0.0.1", "-p", str(ECHO_PORT), "-e", "cat")
    labels: tuple[tuple[str, str], ...] = ()

    @property
    def release(self) -> EngineRelease:
        # The allowlist's, or a candidate under test (CC1).
        return lane_release(self.kind)

    @property
    def image(self) -> str:
        # The engine-only variant where an engine has variants (E0b): runs use another one.
        return self.release.variants.get("engine", self.release.image)


OPENCODE = EngineImage(
    "opencode",
    "/usr/local/bin/opencode",
    ("opencode", "--version"),
    "{version}",
    ("JavaScriptCore", "LGPL-2.0", "oven-sh/WebKit", "tinycc"),
)
_PERL_ECHO = (
    "use IO::Socket::INET; my $s = IO::Socket::INET->new(LocalAddr => '127.0.0.1', LocalPort => %d, Listen => 1, "
    "ReuseAddr => 1) or die; while (my $c = $s->accept) { my $b; print $c $b while sysread($c, $b, 65536); close $c }"
    % ECHO_PORT
)
CODEX = EngineImage(
    "codex",
    "/usr/local/bin/codex-app-server",
    ("codex-app-server", "--version"),
    "codex-app-server {version}",
    ("Apache-2.0", "MPL-2.0", "symphonia 0.6.0", "nucleo-matcher 0.3.1", "bubblewrap"),
    absent=("python3", "bwrap", "codex", "codex-exec", "codex-linux-sandbox"),
)
CLAUDE_CODE = EngineImage(
    "claude_code",
    "/usr/local/bin/claude",
    ("claude", "--version"),
    "{version} (Claude Code)",
    ("does not distribute Claude Code", "Supplied by", "Debian bookworm"),
    absent=("python3", "node", "npm"),
    licence_dir="pxh-claude-code",
    echo_server=("perl", "-e", _PERL_ECHO),
    labels=(("io.pipeshub.engine.supply", "admin"),),
)
ENGINES = [pytest.param(OPENCODE, id="opencode"), pytest.param(CODEX, id="codex"), pytest.param(CLAUDE_CODE, id="claude_code")]


def _image_present(engine: EngineImage) -> tuple[bool, str]:
    try:
        import docker

        client = docker.from_env()
        client.images.get(engine.image)
        client.close()
    except Exception as exc:
        return False, f"needs Docker and {engine.image} (deployment/sandbox-images/{engine.kind}/build.sh): {type(exc).__name__}"
    return True, ""


@pytest.fixture(params=ENGINES)
def engine(request: pytest.FixtureRequest) -> EngineImage:
    chosen: EngineImage = request.param
    present, reason = _image_present(chosen)
    if not present:
        pytest.skip(reason)
    return chosen


@pytest.fixture
async def engine_broker(engine: EngineImage) -> AsyncIterator[DockerSandboxBroker]:
    provider = DockerClientProvider(max_workers=8)
    namespace = f"io.pipeshub.e0test.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e0-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({engine.image}),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    yield broker
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    provider.close()


async def _sandbox(broker: DockerSandboxBroker, engine: EngineImage) -> DockerSandboxHandle:
    spec = SandboxSpec(
        image_digest=engine.image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=512, pids=128, scratch_mb=64),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "E0"},
    )
    return await broker.acquire(spec, lease=LEASE, idempotency_key=f"e0-{uuid.uuid4().hex}")


async def _out(handle: DockerSandboxHandle, argv: list[str]) -> str:
    out, err, code = await run(await handle.exec(argv, env={}), timeout_s=60)
    assert code == 0, err.decode("utf-8", "replace")
    return out.decode("utf-8", "replace").strip()


async def test_im2_the_image_runs_the_pinned_binary_as_the_sandbox_user(
    engine: EngineImage, engine_broker: DockerSandboxBroker
) -> None:
    handle = await _sandbox(engine_broker, engine)
    try:
        assert (await _out(handle, list(engine.version_argv))).splitlines()[-1] == engine.version_output.format(version=engine.release.version.version)
        digest = await _out(handle, ["sha256sum", engine.binary])
        assert "sha256:" + digest.split()[0] == engine.release.version.binary_digest
        assert await _out(handle, ["id", "-u"]) == "1000"
        wanted = ("rm", "rg", "git", "bash", "pxh-relay")
        probe = " ".join((*wanted, *engine.absent))
        present = await _out(handle, ["/bin/sh", "-c", f"for t in {probe}; do command -v $t || echo no-$t; done"])
        assert not any(f"no-{tool}" in present for tool in wanted)
        assert all(f"no-{tool}" in present for tool in engine.absent)
        notices = await _out(handle, ["cat", f"/usr/share/licenses/{engine.licence_dir or 'pxh-' + engine.kind}/NOTICE.md"])
        assert all(marker in notices for marker in engine.notices)
    finally:
        await engine_broker.release(handle)


async def test_im2_labels_name_the_pinned_binary(engine: EngineImage) -> None:
    import docker

    client = docker.from_env()
    try:
        labels = client.images.get(engine.image).labels
    finally:
        client.close()
    assert labels["io.pipeshub.engine.kind"] == engine.kind
    assert labels["io.pipeshub.engine.version"] == engine.release.version.version
    assert labels["io.pipeshub.engine.binary-digest"] == engine.release.version.binary_digest
    for name, value in engine.labels:
        assert labels[name] == value
    # CC1: only a candidate build carries the candidate label.
    candidate = engine.image.endswith(CANDIDATE_TAG_SUFFIX)
    assert labels.get("io.pipeshub.engine.candidate") == ("true" if candidate else None)


async def test_br1_connect_without_python3_uses_the_static_relay(
    engine: EngineImage, engine_broker: DockerSandboxBroker
) -> None:
    handle = await _sandbox(engine_broker, engine)
    try:
        with pytest.raises(SandboxConnectError):
            await handle.connect(ECHO_PORT)
        server = await handle.exec(list(engine.echo_server), env={})
        stream = await connect_when_listening(handle, ECHO_PORT)
        payload = hashlib.sha256(b"e0").hexdigest().encode() * 512
        await stream.write(payload)
        await stream.write_eof()
        echoed = b""
        while len(echoed) < len(payload):
            chunk = await stream.read()
            if not chunk:
                break
            echoed += chunk
        assert echoed == payload
        assert await stream.read() == b""
        await stream.aclose()
        await server.signal(9)
        _ = await collect(server.stdout)
    finally:
        await engine_broker.release(handle)
