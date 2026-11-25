"""ACP-FS-*: an ACP agent's ``fs/*`` served inside the workspace only, in a real sandbox (``workspace_fs``).

The scripts run as the sandbox user in the broker's hardened sandbox, in an Alpine (busybox) engine image and a
Debian (coreutils) one, so both ``readlink -f`` and ``head`` families are covered. Symbolic links the "agent" made
inside the workspace must not carry a read or a write out of it; a plain file in the workspace reads and writes,
with folders created for it. Skipped without a daemon or an image.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.tar import put_config, put_files
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.sandbox.workspace_fs import (
    WorkspacePathRefused,
    read_workspace_text,
    write_workspace_text,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run as finished
from harness_core.workspace.sync.archive import Owner
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

REPO = Path(__file__).resolve().parents[6]
ALLOWLIST = json.loads((REPO / "backend" / "python" / "harness_engines" / "allowlist.json").read_text())
IMAGES = {
    "alpine": next(e for e in ALLOWLIST["engines"] if e["kind"] == "opencode")["image"],
    "debian": next(e for e in ALLOWLIST["engines"] if e["kind"] == "claude_code")["image"],
}
LEASE = LeaseRef("worker-acp-fs", 1)
LIMIT = 1024
SECRET = b"root's config the agent must not read through the client"


async def run(sandbox: DockerSandboxHandle, argv: list[str]) -> tuple[int, bytes, bytes]:
    out, err, code = await finished(await sandbox.exec(argv, env={}))
    return code, out, err


def _present(image: str) -> bool:
    try:
        import docker

        client = docker.from_env()
        client.images.get(image)
        client.close()
    except Exception:
        return False
    return True


@pytest.fixture(params=sorted(IMAGES))
async def sandbox(request: pytest.FixtureRequest) -> AsyncIterator[DockerSandboxHandle]:
    image = IMAGES[request.param]
    if not _present(image):
        pytest.skip(f"needs Docker and {image}")
    provider = DockerClientProvider(max_workers=4)
    namespace = f"io.pipeshub.acpfs.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-acpfs-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset({image}),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    spec = SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.agent": "ACP-FS"},
    )
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"acpfs-{uuid.uuid4().hex}")
    await put_config(handle, {"/state/acp/session.json": SECRET}, homes=("/state/acp",))
    await put_files(handle, {"/workspace/notes.md": b"one\ntwo\n"}, owner=Owner())
    # What a hostile agent can do in its own sandbox: links from the workspace to what it must not reach.
    code, _, _ = await run(
        handle,
        [
            "sh",
            "-c",
            "ln -s /state/acp /workspace/dir-link && ln -s /state/acp/session.json /workspace/file-link "
            "&& ln -s /workspace/notes.md /workspace/inner-link && ln -s /etc /workspace/etc-link",
        ],
    )
    assert code == 0
    try:
        yield handle
    finally:
        await broker.release(handle)
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()


async def test_acp_fs1_a_workspace_file_reads_and_through_an_inner_link(sandbox: DockerSandboxHandle) -> None:
    assert await read_workspace_text(sandbox, "/workspace", "/workspace/notes.md", max_bytes=LIMIT) == b"one\ntwo\n"
    assert await read_workspace_text(sandbox, "/workspace", "/workspace/inner-link", max_bytes=LIMIT) == b"one\ntwo\n"


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/workspace/file-link", "outside_workspace"),
        ("/workspace/dir-link/session.json", "outside_workspace"),
        ("/workspace/etc-link/passwd", "outside_workspace"),
        ("/workspace/missing.md", "not_found"),
        ("/workspace", "outside_workspace"),
        ("/state/acp/session.json", "outside_workspace"),
        ("/workspace/../state/acp/session.json", "not_normalised"),
    ],
)
async def test_acp_fs2_reads_never_leave_the_workspace(sandbox: DockerSandboxHandle, path: str, reason: str) -> None:
    with pytest.raises(WorkspacePathRefused) as refused:
        await read_workspace_text(sandbox, "/workspace", path, max_bytes=LIMIT)
    assert refused.value.reason_code == reason


async def test_acp_fs3_a_file_past_the_limit_is_refused(sandbox: DockerSandboxHandle) -> None:
    await write_workspace_text(sandbox, "/workspace", "/workspace/big.txt", b"x" * (LIMIT + 1), max_bytes=LIMIT * 2)
    with pytest.raises(WorkspacePathRefused) as refused:
        await read_workspace_text(sandbox, "/workspace", "/workspace/big.txt", max_bytes=LIMIT)
    assert refused.value.reason_code == "too_large"


async def test_acp_fs4_a_write_lands_in_new_folders_as_the_sandbox_user(sandbox: DockerSandboxHandle) -> None:
    await write_workspace_text(sandbox, "/workspace", "/workspace/out/deep/report.md", b"done\n", max_bytes=LIMIT)
    assert await read_workspace_text(sandbox, "/workspace", "/workspace/out/deep/report.md", max_bytes=LIMIT) == b"done\n"
    code, out, _ = await run(sandbox, ["stat", "-c", "%u", "/workspace/out/deep/report.md"])
    assert (code, out.strip()) == (0, b"1000")


@pytest.mark.parametrize(
    "path",
    ["/workspace/file-link", "/workspace/dir-link/x.md", "/workspace/dir-link/new/x.md", "/workspace/etc-link/x"],
)
async def test_acp_fs5_writes_never_leave_the_workspace(sandbox: DockerSandboxHandle, path: str) -> None:
    with pytest.raises(WorkspacePathRefused) as refused:
        await write_workspace_text(sandbox, "/workspace", path, b"pwned", max_bytes=LIMIT)
    assert refused.value.reason_code == "outside_workspace"
    code, out, _ = await run(sandbox, ["cat", "/state/acp/session.json"])
    assert (code, out) == (0, SECRET)
    code, _, _ = await run(sandbox, ["sh", "-c", "ls /state/acp/x.md /state/acp/new 2>/dev/null"])
    assert code != 0
