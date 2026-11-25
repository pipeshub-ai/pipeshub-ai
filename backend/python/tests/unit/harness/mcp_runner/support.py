"""Shared set-up for the P5.10 wiring tests: a STDIO instance whose credential resolves by handle (P2.4a),
and a process runner backed by ``SandboxMCPRunner`` over the fake broker running the echo server."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app.api.routes.agent import _resolve_mcp_server_configs
from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.mcp_runner import RunnerConfig, SandboxMCPRunner
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_core.testing.sandbox.mcp_runner import (
    ECHO_COMMAND,
    SECRET_NAME,
    fake_bridge,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.credentials.support import DictConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

    from app.agents.agent_loop.context import AgentContext
    from harness_core.sandbox.mcp_runner.ports import MCPRunner

INSTANCE_ID = "mcp-echo"
TOKEN = "xoxb-" + "5550001112-runnerwiringcanary"
LOG = logging.getLogger("p510")


def stdio_instance(**changes: Any) -> dict[str, Any]:  # noqa: ANN401
    instance: dict[str, Any] = {
        "_id": INSTANCE_ID,
        "orgId": "org-1",
        "createdBy": "user-1",
        "name": "EchoMCP",
        "typeId": "echo",
        "transport": "stdio",
        "authMode": "api_token",
        "command": ECHO_COMMAND,
        "args": [],
        "requiredEnv": [SECRET_NAME],
        "createdAt": 0,
        "updatedAt": 0,
    }
    instance.update(changes)
    return instance


def credential_record(token: str = TOKEN) -> dict[str, Any]:
    return {"isAuthenticated": True, "credentials": {"apiToken": token}}


class ProcessStub:
    """Stands in for ``process_mcp_runner``: hands out the test's runner, or refuses like a missing broker."""

    def __init__(self, runner: MCPRunner | None = None, error: Exception | None = None) -> None:
        self.runner = runner
        self.error = error
        self.calls = 0

    async def get(self, _settings: object) -> MCPRunner:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.runner is not None
        return self.runner


def fake_runner(clock: ManualClock | None = None, **broker_kwargs: Any) -> tuple[SandboxMCPRunner, FakeSandboxBroker]:  # noqa: ANN401
    clock = clock or ManualClock()
    broker = FakeSandboxBroker(clock=clock, programs={**FAKE_PROGRAMS, "python3": fake_bridge()}, **broker_kwargs)
    runner = SandboxMCPRunner(
        broker,
        clock=clock,
        ids=RandomIds(),
        config=RunnerConfig(idle_ttl_s=60, max_age_s=300),
        sealer=CredentialSealer(b"fake-egress-secret"),
    )
    return runner, broker


def runners_on(monkeypatch: pytest.MonkeyPatch, process: ProcessStub) -> None:
    monkeypatch.setenv("MCP_STDIO_RUNNERS", "true")
    monkeypatch.setenv("MCP_RUNNER_IMAGE", "pxh-runner-test:1")
    monkeypatch.setattr("app.harness.mcp_runner.routing.process_mcp_runner", process)


@contextmanager
def echo_declares(hosts: list[str]) -> Iterator[None]:
    """The catalog's ``echo`` entry declaring the hosts its program calls (its runner's egress)."""
    from app.agents.mcp.registry import get_mcp_registry

    registry = get_mcp_registry()
    template = registry.get_template("echo")
    assert template is not None, "the harness conftest registers the echo catalog entry"
    with patch.dict(registry._templates, {"echo": template.model_copy(update={"egress_hosts": hosts})}):  # pyright: ignore[reportPrivateUsage]
        yield


@contextmanager
def resolved_instance(instance: dict[str, Any]) -> Iterator[None]:
    async def resolved(*_: object) -> dict[str, Any]:
        return dict(instance)

    with patch("app.edition_config.get_mcp_instance_resolved", new=resolved):
        yield


async def stdio_context(
    instance: dict[str, Any] | None = None,
    *,
    records: dict[str, dict[str, Any]] | None = None,
    owner: str = "user-1",
) -> tuple[AgentContext, DictConfig]:
    """A request whose STDIO instance went through the chat route's own resolution (a reference, no record)."""
    instance = instance or stdio_instance()
    config = DictConfig(
        {f"/services/mcp/credentials/{INSTANCE_ID}/{who}": record for who, record in (records or {owner: credential_record()}).items()}
    )
    with resolved_instance(instance):
        configs, *_ = await _resolve_mcp_server_configs(
            config,  # type: ignore[arg-type]
            [{"instanceId": INSTANCE_ID, "name": "EchoMCP"}],
            org_id=instance["orgId"],
            credential_lookup_id=owner,
            executing_user_id=owner,
            agent_id="agent-1",
            is_service_account=False,
            logger=LOG,  # type: ignore[arg-type]
        )
    context = make_context(
        config_service=config,
        user_id=owner,
        mcp_servers=[{"instanceId": INSTANCE_ID, "name": "EchoMCP", "displayName": "EchoMCP", "typeId": "echo"}],
        mcp_server_configs=configs,
        run_id="run-1",
    )
    return context, config
