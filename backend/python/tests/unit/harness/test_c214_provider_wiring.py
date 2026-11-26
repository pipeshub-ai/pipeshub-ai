"""C2.14: every PipesHub caller gets its sandboxes through the deployment's provider (SPW-1..SPW-6).

The worker's engine runs, the legacy ``run_code`` path and the MCP runner open ``open_sandbox_provider``; the broker
process builds its backend through ``BROKER_PROVIDERS``. A Kubernetes, managed or microVM deployment is refused with a
plain message where it is first read, never a crash at a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

import app.sandbox_broker_main as broker_main
from app.harness.engines import worker as engine_worker_module
from app.harness.mcp_runner.process import ProcessMCPRunner
from app.harness.sandbox import client as broker_client
from harness_core.sandbox.errors import SandboxProviderUnavailable
from harness_core.sandbox.failover import FailoverSandboxBroker
from harness_core.sandbox.mcp_runner.errors import RunnerUnavailable
from harness_core.sandbox.provider import (
    PROVIDER_ENV,
    BrokerSandboxProvider,
    SandboxProviderKind,
)
from harness_core.sandbox.types import RuntimeMode

PYTHON_ROOT = Path(__file__).resolve().parents[3]


class _Remote:
    def __init__(self, url: str) -> None:
        self.url = url

    def modes(self) -> frozenset[RuntimeMode]:
        return frozenset({RuntimeMode.DOCKER_HARDENED})


class _Config:
    async def get_config(self, key: str, **_: object) -> object:
        return {"sandboxBroker": {"endpoint": "http://broker:8095"}} if key.endswith("endpoints") else None


@dataclass
class _Services:
    def config_service(self) -> object:
        return _Config()


async def test_spw1_a_docker_deployment_reaches_the_broker_through_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_remote(url: str, auth: object, **_: object) -> _Remote:
        del auth
        return _Remote(url)

    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    monkeypatch.setattr(broker_client.RemoteSandboxBroker, "open", open_remote)
    provider = await broker_client.open_sandbox_provider(cast("Any", _Config()))
    assert isinstance(provider, BrokerSandboxProvider)
    assert provider.kind is SandboxProviderKind.DOCKER
    assert isinstance(provider.broker, FailoverSandboxBroker)
    assert provider.modes() == frozenset({RuntimeMode.DOCKER_HARDENED})


@pytest.mark.parametrize("kind", ["k8s", "managed", "microvm"])
async def test_spw2_the_worker_advertises_no_engines_at_once_for_a_provider_it_cannot_reach(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv(engine_worker_module.ENGINE_RUNS_ENV, "true")
    monkeypatch.setenv(PROVIDER_ENV, kind)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    plan = await engine_worker_module.plan_engines(cast("Any", _Services()), sleep=sleep)
    assert plan is None
    # A configuration error is not an outage: no wait for a broker that would never change it.
    assert sleeps == []


async def test_spw3_run_code_and_the_mcp_runner_share_the_process_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[object] = []

    async def open_provider(config_service: object) -> str:
        opened.append(config_service)
        return "provider"

    monkeypatch.setattr(broker_client, "open_sandbox_provider", open_provider)
    monkeypatch.setattr("app.harness.assembly.services.HarnessServices", lambda: _Services())
    process = broker_client.ProcessBrokerClient()
    assert await process.get() == "provider"
    assert await process.get() == "provider"
    assert len(opened) == 1


async def test_spw4_the_mcp_runner_names_the_provider_it_cannot_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse() -> object:
        raise SandboxProviderUnavailable(
            "The Kubernetes sandbox provider is not available in this deployment", reason="provider_unavailable"
        )

    monkeypatch.setattr("app.harness.mcp_runner.process.process_broker_client.get", refuse)
    with pytest.raises(RunnerUnavailable) as refused:
        await ProcessMCPRunner().get(cast("Any", object()))
    assert refused.value.cause == "provider_unavailable"
    assert "Kubernetes sandbox provider is not available" in str(refused.value)


@pytest.mark.parametrize("kind", ["k8s", "managed", "microvm", "dokcer"])
async def test_spw5_the_broker_process_refuses_to_start_for_a_provider_it_cannot_run(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv(PROVIDER_ENV, kind)

    def no_services() -> object:
        raise AssertionError("nothing is built for a provider the broker cannot run")

    monkeypatch.setattr("app.harness.assembly.services.HarnessServices", no_services)
    with pytest.raises(SystemExit) as stopped:
        await broker_main.run_broker(port=0)
    assert stopped.value.code == broker_main.EX_CONFIG
    with pytest.raises(SandboxProviderUnavailable):
        await broker_main.create_service(
            cast("Any", _Config()), provider=cast("Any", None), clock=cast("Any", None), ids=cast("Any", None),
            kind=SandboxProviderKind.KUBERNETES,
        )


# Where a sandbox backend may be constructed: the two factories' builders.
_BUILDERS = {"app/harness/sandbox/client.py", "app/sandbox_broker_main.py"}
_CONSTRUCTION = re.compile(
    r"RemoteSandboxBroker\.open\(|DockerSandboxBroker\.create\(|FailoverSandboxBroker\(|\bopen_engine_broker\(|\bopen_broker_client\("
)


def test_spw6_no_production_caller_builds_a_sandbox_backend_outside_the_factories() -> None:
    found: list[str] = []
    for root in ("app", "harness_core", "harness_engines"):
        for path in sorted((PYTHON_ROOT / root).rglob("*.py")):
            relative = path.relative_to(PYTHON_ROOT).as_posix()
            if "/testing/" in relative or relative in _BUILDERS:
                continue
            text = path.read_text(encoding="utf-8")
            for match in _CONSTRUCTION.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                if not text.splitlines()[line - 1].lstrip().startswith(("def ", "async def ", "class ", "#")):
                    found.append(f"{relative}:{line}: {match.group(0)}")
    assert found == []
