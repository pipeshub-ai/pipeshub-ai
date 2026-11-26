"""Which code sandbox today's loop gets in each test lane.

``run_code`` goes through the product's sandbox bridge either way; only the
backend differs:

* ``host`` (offline lane): the local backend, but the program runs on the
  interpreter running the tests instead of a fresh venv. A fresh venv has no
  pandas or office libraries and would need PyPI on every run; the host
  interpreter has them (backend dependencies), so the lane is deterministic
  and offline.
* ``docker`` (Docker lane): the Docker backend with the production sandbox
  image, as a deployment runs it.
* ``broker`` (Docker lane, P5.11): the broker backend (``SANDBOX_MODE=broker``,
  the shipped default) over a Docker broker in this process, in its own label
  namespace and ``pxh-`` volume prefix, its egress network a rig /28; every
  sandbox it made is reaped when the run ends.
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

SandboxKind = Literal["host", "docker", "broker"]
DOCKER_IMAGE = "pipeshub/sandbox:latest"
# What the rig's containers, volumes and networks carry on a shared daemon.
RIG_OWNER = {"pipeshub.harness.owner": "x-lead"}


def _image() -> str:
    return os.environ.get("SANDBOX_TEST_IMAGE", DOCKER_IMAGE)


def _egress_network(suffix: str) -> str:
    return f"pxh-sbx-egress-{suffix}"


def sandbox_env(kind: SandboxKind, *, suffix: str = "") -> dict[str, str]:
    env = {"PIPESHUB_ENABLE_CODE_EXECUTION": "true"}
    if kind == "docker":
        env |= {"SANDBOX_MODE": "docker", "SANDBOX_DOCKER_IMAGE": _image()}
    elif kind == "broker":
        env |= {
            "SANDBOX_MODE": "broker",
            "SANDBOX_DOCKER_IMAGE": _image(),
            "SANDBOX_EGRESS_NETWORK": _egress_network(suffix),
        }
    else:
        # main #3803: the local backend also needs the operator's explicit opt-in.
        env |= {"SANDBOX_MODE": "local", "SANDBOX_ALLOW_LOCAL": "true"}
    return env


async def _venv_ready(_self: object) -> None:
    return None


@contextmanager
def sandbox_backend(kind: SandboxKind) -> Iterator[None]:
    if kind != "host":
        yield
        return
    from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
    from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

    host = LocalCodingSandbox.describe_capabilities()
    # The lane stands in for the offline sandbox a deployment runs (docker/broker): its code reads only the case's
    # files, so run_code keeps the sink a deployment's offline sandbox gives it (SINK2, BUGS-B-1).
    offline = classmethod(lambda _cls: host.model_copy(update={"supports_network": False}))
    with (
        patch.object(EnvironmentManager, "python_bin", property(lambda _self: sys.executable)),
        patch.object(EnvironmentManager, "ensure_python_venv", _venv_ready),
        patch.object(LocalCodingSandbox, "describe_capabilities", offline),
    ):
        yield


@asynccontextmanager
async def broker_lane() -> AsyncIterator[str]:
    """An in-process Docker broker for one run; yields the suffix ``sandbox_env("broker", suffix=…)`` names."""
    import docker

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.clock import SystemClock
    from harness_core.ids import RandomIds
    from tests.support.docker_networks import create_rig_network

    suffix = uuid.uuid4().hex[:8]
    client = docker.from_env()
    network = _egress_network(suffix)
    _ = create_rig_network(client, network, labels={**RIG_OWNER, "pxh.sbx": suffix})
    provider = DockerClientProvider(max_workers=8)
    try:
        broker = await DockerSandboxBroker.create(
            provider=provider,
            clock=SystemClock(),
            ids=RandomIds(),
            allowed_images=frozenset({_image()}),
            helper_image=_image(),
            egress_network=network,
            label_namespace=f"io.pipeshub.sbx.{suffix}",
            volume_prefix=f"pxh-sbx-{suffix}",
        )

        async def source() -> DockerSandboxBroker:
            return broker

        try:
            with patch("app.harness.sandbox.client.process_broker_client.get", new=source):
                yield suffix
        finally:
            _ = await broker.reap(labels={}, older_than_s=0)
    finally:
        client.networks.get(network).remove()
        provider.close()


__all__ = ["DOCKER_IMAGE", "SandboxKind", "broker_lane", "sandbox_backend", "sandbox_env"]
