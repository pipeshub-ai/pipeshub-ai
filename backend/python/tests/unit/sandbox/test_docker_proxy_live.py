"""The real sandbox code paths, driven through the Docker API proxy.

Skipped unless a daemon and the sandbox image are present. Run with:
    SANDBOX_TEST_IMAGE=pipeshub/sandbox:latest pytest \\
        tests/unit/sandbox/test_docker_proxy_live.py -m docker --timeout=600
On a daemon other people use, also name a network and a proxy instance of
your own with SANDBOX_TEST_EGRESS_NETWORK and SANDBOX_TEST_PROXY_INSTANCE.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.sandbox.docker_proxy import DockerApiPolicy, DockerSocketProxy

from ..agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    docker_available,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from app.agent_loop_lib.sandbox.coding.base import CodeResult

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

EGRESS = os.environ.get("SANDBOX_TEST_EGRESS_NETWORK") or "pipeshub_sandbox_egress"
INSTANCE = os.environ.get("SANDBOX_TEST_PROXY_INSTANCE") or "default"


@pytest.fixture
def proxy_host(monkeypatch) -> Iterator[str]:
    available, reason = docker_available()
    if not available:
        pytest.skip(reason)

    loop = asyncio.new_event_loop()
    policy = DockerApiPolicy(
        allowed_images=frozenset({DOCKER_TEST_IMAGE}), allowed_networks=frozenset({EGRESS}),
    )
    proxy = DockerSocketProxy(policy, "/var/run/docker.sock", instance=INSTANCE)
    server = loop.run_until_complete(asyncio.start_server(proxy.handle, "127.0.0.1", 0))
    port = server.sockets[0].getsockname()[1]
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    host = f"tcp://127.0.0.1:{port}"
    monkeypatch.setenv("DOCKER_HOST", host)
    yield host

    loop.call_soon_threadsafe(server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


async def _run_code_through_proxy(proxy_host: str, tmp_path: Path, code: str) -> CodeResult:
    from app.agent_loop_lib.sandbox.coding.base import CodeRequest
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider

    provider = DockerClientProvider(max_workers=2)
    sb = DockerCodingSandbox(
        working_dir=str(tmp_path / "wd"), image=DOCKER_TEST_IMAGE,
        image_node_modules="/home/sandbox/node_modules", provider=provider,
        egress_network=EGRESS,
    )
    await sb.provision()
    try:
        result = await sb.execute(CodeRequest(code=code, language="python"))
        assert result.exit_code == 0, result.stderr
        return result
    finally:
        await sb.destroy()
        provider.close()


async def test_coding_sandbox_runs_and_installs_through_proxy(proxy_host, tmp_path) -> None:
    from app.agent_loop_lib.sandbox.coding.base import CodeRequest
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider

    provider = DockerClientProvider(max_workers=2)
    sb = DockerCodingSandbox(
        working_dir=str(tmp_path / "wd"), image=DOCKER_TEST_IMAGE,
        image_node_modules="/home/sandbox/node_modules", provider=provider,
        allow_network=True, egress_network=EGRESS,
    )
    await sb.provision()
    try:
        install = await sb.install_packages(["six"], "python")
        assert install.success, install.stderr
        result = await sb.execute(CodeRequest(
            code=(
                "import os, six\n"
                "open(os.path.join(os.environ['OUTPUT_DIR'], 'r.txt'), 'w').write('ok')\n"
                "print('six:' + six.__version__)\n"
            ),
            language="python",
        ))
        assert result.exit_code == 0, result.stderr
        assert "six:" in result.stdout
        assert await sb.download_file("output/r.txt") == b"ok"
    finally:
        await sb.destroy()
        provider.close()


async def test_legacy_executor_runs_through_proxy(proxy_host, tmp_path) -> None:
    from app.sandbox import docker_executor

    with patch.object(docker_executor, "SANDBOX_IMAGE", DOCKER_TEST_IMAGE), \
            patch.object(docker_executor, "_SANDBOX_ROOT", str(tmp_path / "root")):
        result = await docker_executor.DockerExecutor(egress_network=EGRESS).execute(
            "print(6 * 7)", "python", timeout_seconds=60,
        )
    assert result.success, (result.error, result.stderr)
    assert "42" in result.stdout


def test_escape_attempts_are_refused(proxy_host) -> None:
    import docker
    from docker.errors import APIError

    client = docker.DockerClient(base_url=proxy_host)
    try:
        attempts = [
            {"privileged": True, "network_mode": "none"},
            {"volumes": {"/": {"bind": "/host", "mode": "rw"}}, "network_mode": "none"},
            {"pid_mode": "host", "network_mode": "none"},
            {"network_mode": "host"},
            {"cap_add": ["SYS_ADMIN"], "network_mode": "none"},
        ]
        for kwargs in attempts:
            with pytest.raises(APIError) as exc:
                client.containers.create(DOCKER_TEST_IMAGE, ["true"], **kwargs)
            assert exc.value.status_code == 403, kwargs

        with pytest.raises(APIError) as exc:
            client.containers.list()
        assert exc.value.status_code == 403
        with pytest.raises(APIError) as exc:
            client.images.pull("alpine", tag="latest")
        assert exc.value.status_code == 403
    finally:
        client.close()


def test_info_answers_only_the_sizing_fields(proxy_host) -> None:
    import docker

    from app.sandbox.docker_proxy import INFO_FIELDS

    client = docker.DockerClient(base_url=proxy_host)
    try:
        info = client.info()
    finally:
        client.close()
    assert info["NCPU"] > 0 and info["MemTotal"] > 0
    assert set(info) <= set(INFO_FIELDS)
    assert "HttpProxy" not in info


def test_host_route_matches_this_kernels_boot_id(proxy_host) -> None:
    import docker

    from app.agent_loop_lib.sandbox.host_reading import boot_id_hash

    client = docker.DockerClient(base_url=proxy_host)
    try:
        response = client.api.get(client.api._url("/_pipeshub/host"), timeout=5)
    finally:
        client.close()
    assert response.status_code == 200
    assert response.json()["bootIdHash"] == boot_id_hash()


async def test_sandbox_containers_are_marked_first_to_kill(proxy_host, tmp_path) -> None:
    """Read from inside the run: the container is removed afterwards."""
    result = await _run_code_through_proxy(
        proxy_host, tmp_path, "print(open('/proc/self/oom_score_adj').read().strip())",
    )
    assert result.stdout.strip() == "1000"


def test_containers_the_proxy_did_not_create_are_untouchable(proxy_host) -> None:
    import docker
    from docker.errors import APIError

    direct = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    victim = direct.containers.create(DOCKER_TEST_IMAGE, ["true"], network_mode="none")
    proxied = docker.DockerClient(base_url=proxy_host)
    try:
        for call in (
            lambda: proxied.api.inspect_container(victim.id),
            lambda: proxied.api.start(victim.id),
            lambda: proxied.api.put_archive(victim.id, "/", b""),
            lambda: proxied.api.remove_container(victim.id, force=True),
        ):
            with pytest.raises(APIError) as exc:
                call()
            assert exc.value.status_code == 403
    finally:
        victim.remove(force=True)
        direct.close()
        proxied.close()


def _start_proxy(instance: str) -> tuple[str, DockerSocketProxy, asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    policy = DockerApiPolicy(
        allowed_images=frozenset({DOCKER_TEST_IMAGE}), allowed_networks=frozenset({EGRESS}),
    )
    proxy = DockerSocketProxy(policy, "/var/run/docker.sock", instance=instance)
    server = loop.run_until_complete(asyncio.start_server(proxy.handle, "127.0.0.1", 0))
    port = server.sockets[0].getsockname()[1]
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return f"tcp://127.0.0.1:{port}", proxy, loop, thread


def test_reaper_removes_only_its_own_stacks_old_containers() -> None:
    import time
    import uuid

    import docker
    from docker.errors import APIError

    from app.sandbox.docker_proxy import Reaper, ReaperSettings

    available, reason = docker_available()
    if not available:
        pytest.skip(reason)
    suffix = uuid.uuid4().hex[:8]
    host_a, proxy_a, loop_a, thread_a = _start_proxy(f"it-a-{suffix}")
    host_b, _proxy_b, loop_b, thread_b = _start_proxy(f"it-b-{suffix}")
    client_a, client_b = docker.DockerClient(base_url=host_a), docker.DockerClient(base_url=host_b)
    limits = {"mem_limit": 64 * 1024 * 1024, "nano_cpus": 100_000_000, "pids_limit": 16, "network_mode": "none"}
    a = client_a.containers.create(DOCKER_TEST_IMAGE, ["sleep", "100"], **limits)
    b = client_b.containers.create(DOCKER_TEST_IMAGE, ["sleep", "100"], **limits)
    try:
        a.start()
        b.start()
        with pytest.raises(APIError) as exc:
            client_a.api.inspect_container(b.id)
        assert exc.value.status_code == 403

        time.sleep(3)
        reaper = Reaper(proxy_a, ReaperSettings(reap_after_s=2, interval_s=1))
        removed = asyncio.run_coroutine_threadsafe(reaper.reap_once(), loop_a).result(30)
        assert removed == 1
        with pytest.raises(APIError):
            client_b.api.inspect_container(a.id)  # gone (and never B's to see)
        assert client_b.api.inspect_container(b.id)["State"]["Running"] is True
    finally:
        for client, cid in ((client_a, a.id), (client_b, b.id)):
            with contextlib.suppress(APIError):
                client.api.remove_container(cid, force=True)
            client.close()
        for loop, thread in ((loop_a, thread_a), (loop_b, thread_b)):
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)


def test_containers_usage_counts_only_this_stacks_containers() -> None:
    import uuid

    import docker
    from docker.errors import APIError

    available, reason = docker_available()
    if not available:
        pytest.skip(reason)
    suffix = uuid.uuid4().hex[:8]
    host_a, proxy_a, loop_a, thread_a = _start_proxy(f"it-a-{suffix}")
    host_b, _proxy_b, loop_b, thread_b = _start_proxy(f"it-b-{suffix}")
    client_a, client_b = docker.DockerClient(base_url=host_a), docker.DockerClient(base_url=host_b)
    direct = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    limits = {"mem_limit": 64 * 1024 * 1024, "nano_cpus": 100_000_000, "pids_limit": 16, "network_mode": "none"}
    hold = ["python3", "-c", "b = b'x' * (20 * 2**20); import time; time.sleep(60)"]
    ours = [client_a.containers.create(DOCKER_TEST_IMAGE, hold, **limits) for _ in range(2)]
    others = [
        client_b.containers.create(DOCKER_TEST_IMAGE, hold, **limits),
        direct.containers.create(DOCKER_TEST_IMAGE, hold, **limits),
    ]
    try:
        for container in (*ours, *others):
            container.start()
        time.sleep(3)
        block, error = asyncio.run_coroutine_threadsafe(proxy_a.containers_usage(), loop_a).result(30)
        assert error is None and block is not None
        assert block["running"] == 2 and block["memWorkingSetBytes"] >= 2 * 20 * 2**20
        response = client_a.api.get(client_a.api._url("/_pipeshub/usage"), timeout=5)
        assert response.json() == {"v": 1, "sidecar": None, "sidecarError": "not_configured"}
    finally:
        for container in (*ours, *others):
            with contextlib.suppress(APIError):
                direct.api.remove_container(container.id, force=True)
        for client in (client_a, client_b, direct):
            client.close()
        for loop, thread in ((loop_a, thread_a), (loop_b, thread_b)):
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)


async def test_stop_removes_the_container_through_the_proxy(proxy_host, tmp_path) -> None:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.agent_loop_lib.sandbox.coding.base import CodeRequest
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider
    from app.agent_loop_lib.sandbox.manager import SandboxCallStopped, SandboxManager

    provider = DockerClientProvider(max_workers=2)
    sb = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), image=DOCKER_TEST_IMAGE, provider=provider)
    await sb.provision()
    token = CancellationToken()
    manager = SandboxManager(stop_token=token)
    asyncio.get_running_loop().call_later(2, token.cancel)
    try:
        with pytest.raises(SandboxCallStopped):
            await asyncio.wait_for(manager.unless_stopped(
                lambda: sb.execute(CodeRequest(code="while True: pass", language="python", timeout=120)),
                tool="run_code",
            ), 15)
    finally:
        await sb.destroy()
        provider.close()


def test_oversized_create_is_refused_and_swap_is_off(proxy_host) -> None:
    import docker
    from docker.errors import APIError

    client = docker.DockerClient(base_url=proxy_host)
    limits = {"nano_cpus": 500_000_000, "pids_limit": 256, "network_mode": "none"}
    try:
        with pytest.raises(APIError) as exc:
            client.containers.create(DOCKER_TEST_IMAGE, ["true"], mem_limit="2g", **limits)
        assert exc.value.status_code == 403
        assert "exceeds the maximum of 1024 MB" in str(exc.value)
        container = client.containers.create(DOCKER_TEST_IMAGE, ["true"], mem_limit=512 * 1024 * 1024, **limits)
        try:
            host = client.api.inspect_container(container.id)["HostConfig"]
            assert host["MemorySwap"] == host["Memory"] == 512 * 1024 * 1024
        finally:
            container.remove(force=True)
    finally:
        client.close()


async def test_out_of_memory_is_detected_through_the_proxy(proxy_host, tmp_path) -> None:
    from app.agent_loop_lib.sandbox.coding.base import CodeRequest, ErrorCategory
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider

    provider = DockerClientProvider(max_workers=2)
    sb = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), image=DOCKER_TEST_IMAGE, provider=provider)
    await sb.provision()
    try:
        result = await sb.execute(CodeRequest(code="x = 'a' * (1024**3)\nprint(len(x))", language="python"))
        assert result.error_analysis is not None
        assert result.error_analysis.category == ErrorCategory.OUT_OF_MEMORY
    finally:
        await sb.destroy()
        provider.close()


def test_another_stack_sees_neither_the_run_nor_the_network(proxy_host) -> None:
    """While this stack's sandbox runs on the egress network, a second
    stack's proxy on the same daemon lists only that network, sees none of
    this stack's containers on it, and cannot remove it."""
    import uuid

    import docker
    from docker.errors import APIError

    from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider
    from app.agent_loop_lib.sandbox.coding.egress_firewall import (
        ensure_egress_network_sync,
    )

    provider = DockerClientProvider(max_workers=1)
    try:
        assert asyncio.run(provider.egress_network_cidrs(EGRESS))
    finally:
        provider.close()
    host_b, _proxy_b, loop_b, thread_b = _start_proxy(f"{INSTANCE}-b-{uuid.uuid4().hex[:6]}")
    ours = docker.DockerClient(base_url=proxy_host)
    theirs = docker.DockerClient(base_url=host_b)
    limits = {"mem_limit": 64 * 1024 * 1024, "nano_cpus": 100_000_000, "pids_limit": 16}
    run = ours.containers.create(DOCKER_TEST_IMAGE, ["sleep", "60"], network=EGRESS, **limits)
    try:
        run.start()
        mine = ours.api.inspect_network(EGRESS)
        assert run.id in mine["Containers"]
        assert mine["Labels"]["ai.pipeshub.docker-proxy.instance"] == INSTANCE

        seen = theirs.api.inspect_network(EGRESS)
        assert seen["Containers"] == {}
        assert run.id.encode() not in theirs.api.get(theirs.api._url(f"/networks/{seen['Id']}")).content
        assert [n["Name"] for n in theirs.api.networks()] == [EGRESS]
        with pytest.raises(APIError) as exc:
            theirs.api.remove_network(EGRESS)
        assert exc.value.status_code == 403
        # The other stack's app keeps the network it cannot recreate.
        assert ensure_egress_network_sync(theirs, EGRESS, {}) != []
        with pytest.raises(APIError) as exc:
            theirs.api.create_container(DOCKER_TEST_IMAGE, ["true"], name=run.name)
        assert exc.value.status_code == 403 and run.id not in str(exc.value)
        # A pull of every tag is refused before it reaches the daemon.
        pulled = theirs.api.post(
            theirs.api._url("/images/create"), params={"fromImage": DOCKER_TEST_IMAGE.rsplit(":", 1)[0]},
        )
        assert pulled.status_code == 403
    finally:
        with contextlib.suppress(APIError):
            run.remove(force=True)
        ours.close()
        theirs.close()
        loop_b.call_soon_threadsafe(loop_b.stop)
        thread_b.join(timeout=5)
