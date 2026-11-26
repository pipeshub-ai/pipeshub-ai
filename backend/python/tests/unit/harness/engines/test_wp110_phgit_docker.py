"""WP-110 PHGIT-D1..D3 on the real lane (runc): a real engine image's git against a handed-over copy.

The hand-off is the production one: ``_tar_bare``'s tar seeded by ``DockerSandboxBroker.seed_tar`` into the sandbox's
read-only repository folder, and the session's ``ph.py`` checking it out as the sandbox user. Run on the claude_code
image (Debian bookworm, git 2.39) and the opencode image (Alpine, git 2.54), the two git lines the engine images ship.

- PHGIT-D1: the copy belongs to the sandbox user, and ``ph git clone`` and ``ph git fetch`` check it out; the copy
  stays read-only to that user (no new file, no mode change).
- PHGIT-D2: a copy owned by another uid (root, as every hand-off before WP-110) fails with git's reason and the plain
  cause, never git's last line alone.
- PHGIT-D3: on git 2.39, ``-c safe.directory`` and ``GIT_CONFIG_COUNT`` naming that copy do not rescue it (git clears
  both for the local ``upload-pack``), which is why the fix is the owner and not configuration.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.repos.fetch import _tar_bare  # pyright: ignore[reportPrivateUsage]
from harness_core.sandbox.tar import config_tar
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.workspace.sync.archive import Owner
from tests.support.git_host import commit, git, working_copy
from tests.unit.harness.engines.lane_releases import lane_release

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.ports import SandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

SESSION = "/pipeshub/session"
REPOS = "/pipeshub/repos"
WORKSPACE = "/workspace"
PH = Path(__file__).resolve().parents[4] / "harness_engines" / "pipeshub" / "ph.py"
KINDS = ("claude_code", "opencode")
CHECKOUT = r"""
import subprocess, sys
sys.path.insert(0, '/pipeshub/session')
import ph
source, target = sys.argv[1], sys.argv[2]
try:
    ph.checkout_clone(source, target, 'https://github.com/acme/widgets', None, full=False)
    ph.checkout_fetch(source, target)
except ph.PhError as failed:
    print(str(failed), file=sys.stderr)
    sys.exit(failed.code)
print(subprocess.run(['git', '-C', target, 'rev-parse', 'origin/main'], capture_output=True, text=True).stdout.strip())
"""


async def _one(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


@pytest.fixture(params=KINDS)
async def sandbox(request: pytest.FixtureRequest) -> AsyncIterator[SandboxHandle]:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.ids import RandomIds
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.conftest import cleanup

    image = lane_release(request.param).image
    try:
        import docker

        client = docker.from_env()
        try:
            client.images.get(image)
        finally:
            client.close()
    except Exception as exc:
        pytest.skip(f"needs Docker and {image}: {type(exc).__name__}")
    namespace = f"io.pipeshub.wp110.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-wp110-" + namespace.rsplit(".", 1)[1]
    provider = DockerClientProvider(max_workers=8)
    broker: DockerSandboxBroker = await DockerSandboxBroker.create(
        provider=provider, clock=ManualClock(), ids=RandomIds(), allowed_images=frozenset({image}),
        label_namespace=namespace, volume_prefix=prefix,
    )
    spec = SandboxSpec(
        image_digest=image, mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=512, pids=128, scratch_mb=64),
        mounts=(MountSpec(WORKSPACE), MountSpec(SESSION, read_only=True), MountSpec(REPOS, read_only=True, size_mb=32)),
        labels={"pxh.test": "wp110"},
    )
    handle = await broker.acquire(spec, lease=LeaseRef("worker-1", 1), idempotency_key=f"wp110-{uuid.uuid4().hex[:8]}")
    try:
        await handle.seed_tar(SESSION, config_tar({"ph.py": PH.read_bytes()}))
        yield handle
    finally:
        await broker.release(handle)
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()


async def _sh(sandbox: SandboxHandle, argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    process = await sandbox.exec(argv, env={"HOME": "/tmp", **(env or {})})
    out = b"".join([chunk async for chunk in process.stdout])
    err = b"".join([chunk async for chunk in process.stderr])
    return await process.wait(timeout_s=300), out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _copy(tmp_path: Path, name: str, owner: Owner | None) -> tuple[bytes, str]:
    seed = working_copy(tmp_path / f"seed-{name}")
    head = commit(seed, "README.md", "hello\n")
    bare = tmp_path / f"{name}.git"
    git("clone", "-q", "--bare", "--template=", str(seed), str(bare))
    out = tmp_path / f"{name}.tar"
    _tar_bare(bare, out, f"{name}.git", owner)
    return out.read_bytes(), head


async def test_phgit_d1_the_sandbox_users_copy_is_checked_out_by_ph_and_stays_read_only(
    sandbox: SandboxHandle, tmp_path: Path
) -> None:
    tar, head = _copy(tmp_path, "widgets", None)
    await sandbox.seed_tar(REPOS, _one(tar))
    copy = f"{REPOS}/widgets.git"
    code, owner, _ = await _sh(sandbox, ["stat", "-c", "%u:%g", copy])
    assert code == 0 and owner.strip() == f"{Owner().uid}:{Owner().gid}"
    code, out, err = await _sh(sandbox, ["python3", "-c", CHECKOUT, copy, f"{WORKSPACE}/widgets"])
    assert code == 0, err
    assert out.strip().splitlines()[-1] == head
    for argv in (["touch", f"{copy}/planted"], ["chmod", "u+w", f"{copy}/HEAD"], ["git", "--git-dir", copy, "config", "x.y", "z"]):
        code, _, _ = await _sh(sandbox, argv)
        assert code != 0, f"{argv} changed the handed-over copy"


async def test_phgit_d2_d3_a_copy_another_uid_owns_fails_in_plain_words_and_config_does_not_rescue_it_on_git_239(
    sandbox: SandboxHandle, tmp_path: Path
) -> None:
    tar, _ = _copy(tmp_path, "rooted", Owner(0, 0))
    await sandbox.seed_tar(REPOS, _one(tar))
    copy = f"{REPOS}/rooted.git"
    code, _, err = await _sh(sandbox, ["python3", "-c", CHECKOUT, copy, f"{WORKSPACE}/rooted"])
    assert code == 1, err
    assert "ph: git clone failed: fatal: detected dubious ownership" in err and "PipesHub fault" in err, err
    assert "and the repository exists." not in err
    _, version, _ = await _sh(sandbox, ["git", "--version"])
    if not version.strip().startswith("git version 2.39."):
        return
    for argv, env in (
        (["git", "-c", f"safe.directory={copy}", "clone", "-q", copy, f"{WORKSPACE}/c1"], {}),
        (["git", "clone", "-q", copy, f"{WORKSPACE}/c2"],
         {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": copy}),
    ):
        code, _, err = await _sh(sandbox, argv, env)
        assert code != 0 and "dubious ownership" in err, (argv, err)
