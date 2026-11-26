"""E1 (from the stage-1 E2E): a session's persistent folders on the broker's data volume go with the
session, scoped by install and org.

The Docker daemon, and so the data volume, can be shared by several installs: each broker keeps its folders
under ``<install>/`` and erases only there.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import docker.types
import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode
from harness_core.testing.sandbox.contract import run, with_mounts
from harness_core.testing.sandbox.erase_contract import SessionFolderEraserContract

from .conftest import AGENT_LABEL, DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.testing.clock import ManualClock

    from .conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300)]


@pytest.fixture
def runtime_mode() -> RuntimeMode:
    # Erasure runs a helper, never a sandbox runtime: runc is enough.
    return RuntimeMode.DOCKER_HARDENED


@pytest.fixture
def data_volume(provider: DockerClientProvider, volume_prefix: str) -> Iterator[str]:
    name = f"{volume_prefix}-data"
    provider.client.api.create_volume(name=name, labels=dict(AGENT_LABEL))
    yield name
    provider.client.api.remove_volume(name, force=True)


async def _broker(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str, data_volume: str, install: str
) -> DockerSandboxBroker:
    return await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        data_volume=data_volume, allowed_images=frozenset({DOCKER_TEST_IMAGE}), install_id=install,
    )


def _listing(provider: DockerClientProvider, volume: str) -> set[str]:
    out = provider.client.containers.run(
        DOCKER_TEST_IMAGE, ["sh", "-c", "cd /data && find . -name note.txt"], remove=True, network_mode="none",
        labels=dict(AGENT_LABEL), mounts=[docker.types.Mount("/data", volume, type="volume", read_only=True)],
    )
    return {line.removeprefix("./") for line in out.decode().split()}


class TestDockerSessionFolderEraser(SessionFolderEraserContract):
    @pytest.fixture
    async def eraser(
        self, provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
        cleanup_namespace: None, data_volume: str,
    ) -> Any:  # noqa: ANN401
        return await _broker(provider, clock, namespace, volume_prefix, data_volume, f"inst-{uuid.uuid4().hex[:8]}")


async def test_two_installs_on_one_daemon_never_erase_each_others_folders(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, data_volume: str, sandbox_env: DockerContractEnv,
) -> None:
    ours = await _broker(provider, clock, namespace, volume_prefix, data_volume, "inst-a")
    theirs = await _broker(provider, clock, namespace, volume_prefix, data_volume, "inst-b")
    source = "org-1/ses-1/workspace"
    for index, broker in enumerate((ours, theirs)):
        spec = with_mounts(sandbox_env.spec(), MountSpec("/ws", source=source))
        handle = await broker.acquire(spec, lease=LeaseRef("w", 1), idempotency_key=f"k-{index}")
        _, err, code = await run(await handle.exec(sandbox_env.argv_write_file("/ws/note.txt", "x"), env={}))
        assert code == 0, err
        await broker.release(handle)
    # Each install's folders sit under its own id: the same org and session never meet.
    assert _listing(provider, data_volume) == {f"inst-a/{source}/note.txt", f"inst-b/{source}/note.txt"}
    assert await ours.erase_session(org_id="org-1", session_id="ses-1") == 1
    assert _listing(provider, data_volume) == {f"inst-b/{source}/note.txt"}
    assert await ours.erase_session(org_id="org-1", session_id="ses-1") == 0
    assert _listing(provider, data_volume) == {f"inst-b/{source}/note.txt"}


async def test_cleanup_g1_erasing_an_org_in_one_install_leaves_the_same_org_in_another_install_whole(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, data_volume: str, sandbox_env: DockerContractEnv,
) -> None:
    ours = await _broker(provider, clock, namespace, volume_prefix, data_volume, "inst-a")
    theirs = await _broker(provider, clock, namespace, volume_prefix, data_volume, "inst-b")
    source = "org-1/ses-1/workspace"
    for index, broker in enumerate((ours, theirs)):
        spec = with_mounts(sandbox_env.spec(), MountSpec("/ws", source=source))
        handle = await broker.acquire(spec, lease=LeaseRef("w", 1), idempotency_key=f"k-{index}")
        _, err, code = await run(await handle.exec(sandbox_env.argv_write_file("/ws/note.txt", "x"), env={}))
        assert code == 0, err
        await broker.release(handle)
    live = await theirs.acquire(sandbox_env.spec(labels={"orgId": "org-1"}), lease=LeaseRef("w", 1), idempotency_key="k-live")
    try:
        erased = await ours.erase_org(org_id="org-1")
        assert (erased.sandboxes, erased.folders) == (0, 1)
        assert _listing(provider, data_volume) == {f"inst-b/{source}/note.txt"}
        _, _, code = await run(await live.exec(sandbox_env.argv_print("x", "", 0), env={}))
        assert code == 0, "another install's sandbox of the same org keeps running"
    finally:
        await theirs.release(live)


def test_an_install_id_is_one_safe_segment(provider: DockerClientProvider, clock: ManualClock) -> None:
    for bad in ("..", "a/b", ""):
        with pytest.raises(ValueError, match="install id"):
            DockerSandboxBroker(
                provider=provider, clock=clock, ids=RandomIds(), capabilities=None,  # type: ignore[arg-type]
                install_id=bad,
            )
