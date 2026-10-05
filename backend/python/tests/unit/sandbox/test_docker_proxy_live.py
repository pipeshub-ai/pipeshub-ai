"""The real sandbox code paths, driven through the Docker API proxy.

Skipped unless a daemon and the sandbox image are present. Run with:
    SANDBOX_TEST_IMAGE=pipeshub/sandbox:latest pytest \\
        tests/unit/sandbox/test_docker_proxy_live.py -m docker --timeout=600
"""

from __future__ import annotations

import asyncio
import threading
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

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

EGRESS = "pipeshub_sandbox_egress"


@pytest.fixture
def proxy_host(monkeypatch) -> Iterator[str]:
    available, reason = docker_available()
    if not available:
        pytest.skip(reason)

    loop = asyncio.new_event_loop()
    policy = DockerApiPolicy(
        allowed_images=frozenset({DOCKER_TEST_IMAGE}), allowed_networks=frozenset({EGRESS}),
    )
    proxy = DockerSocketProxy(policy, "/var/run/docker.sock")
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


async def test_legacy_executor_runs_through_proxy(proxy_host) -> None:
    from app.sandbox import docker_executor

    with patch.object(docker_executor, "SANDBOX_IMAGE", DOCKER_TEST_IMAGE):
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
