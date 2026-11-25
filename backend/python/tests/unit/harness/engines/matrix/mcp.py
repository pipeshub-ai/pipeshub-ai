"""The matrix's MCP server: the repo's stdlib echo server in a P5.10 runner on the Docker broker, behind P2.8's hub.

The server is pinned in an image built ``FROM`` the sandbox test image (one layer, removed at teardown), as D23
requires; ``MCP_STDIO_RUNNERS`` and ``MCP_HUB`` are on, so the agent's tool path (``MCPToolProvider``) starts it in
its own container and admits only the tools whose manifest an admin approved.
"""

from __future__ import annotations

import contextlib
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app import edition_config
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.constants.mcp_server_constants import MCP_ROOT
from app.agents.mcp.registry import get_mcp_registry
from app.api.routes.agent import (
    _resolve_mcp_server_configs,  # pyright: ignore[reportPrivateUsage]
)
from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.mcp_runner import RunnerConfig, SandboxMCPRunner
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.mcp_runner import SECRET_NAME
from tests.support import docker_session
from tests.support.docker_images import build_test_image
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.engines.real_stack import OWNER
from tests.unit.harness.mcp_hub.support import hub_on
from tests.unit.harness.mcp_runner.support import (
    INSTANCE_ID,
    ProcessStub,
    runners_on,
    stdio_instance,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    import pytest

    from tests.support.config_service import FakeConfigurationService
    from tests.unit.harness.engines.matrix.org import Org

SERVER_PATH = "/opt/pxh-echo/server.py"
ECHO_SOURCE = Path(__file__).resolve().parents[5] / "harness_core/testing/sandbox/mcp_echo_server.py"
BASE_IMAGE = os.environ.get("SANDBOX_TEST_IMAGE", "pxh-sandbox:pr1")
# The tools the agent's MCP server offers (``mcp_echo_server``); the matrix approves ``echo`` only.
SERVER_TOOLS = ("echo", "env_digest", "fetch", "where")
# Where a policy that requires an admin's approval keeps it and the drift it saw: the lane writes the former as an admin
# would and reads the latter, so its MC rows run only where such a policy is bound.
MANIFEST_PATH = f"{MCP_ROOT}/manifests/{{}}"
DRIFT_PATH = f"{MCP_ROOT}/manifest-drift/{{}}"
LOG = logging.getLogger("e2e3.mcp")
# The matrix lane's ``docker_session`` suite: its runners, image and Neo4j are reaped together.
SUITE = "e2e3"


def build_runner_image(client: Any, base: str = BASE_IMAGE) -> str:  # noqa: ANN401 - docker.DockerClient
    tag = f"pxh-mcp-runner:e2e3-{uuid.uuid4().hex[:8]}"
    dockerfile = f"FROM {base}\nCOPY server.py {SERVER_PATH}\n"
    return build_test_image(
        client, tag, dockerfile, files={"server.py": ECHO_SOURCE.read_bytes()},
        labels={**OWNER, **docker_session.labels(SUITE)},
    )


@contextlib.asynccontextmanager
async def echo_runner(image: str) -> AsyncIterator[SandboxMCPRunner]:
    """A runner on its own broker and label namespace, removed by it at the end."""
    namespace = f"io.pipeshub.e2e3mcp.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e2e3mcp-" + namespace.rsplit(".", 1)[1]
    provider = DockerClientProvider(max_workers=8)
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=SystemClock(), ids=RandomIds(), allowed_images=frozenset({image}),
        label_namespace=namespace, volume_prefix=prefix,
    )
    config = RunnerConfig(idle_ttl_s=600, max_age_s=3600, reap_grace_s=60, ready_timeout_s=60, labels={**OWNER, "pipeshub.agent": "E2E3", **docker_session.labels(SUITE)},
    )
    # The server lists no host, so its secret goes nowhere: it holds a placeholder sealed under a deployment key (WP-06).
    runner = SandboxMCPRunner(
        broker, clock=SystemClock(), ids=RandomIds(), config=config, instance="e2e3",
        sealer=CredentialSealer(b"e2e3-deployment-key"),
    )
    try:
        yield runner
    finally:
        with contextlib.suppress(Exception):
            _ = await runner.stop_all()
        await broker.aclose()
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()


def instance(org: Org) -> dict[str, Any]:
    return stdio_instance(command="python3", args=[SERVER_PATH], orgId=org.org_id, createdBy=org.user_id("alice"))


@contextlib.contextmanager
def mcp_on(
    patch: pytest.MonkeyPatch, runner: SandboxMCPRunner, image: str, org: Org, *, admission: object | None = None
) -> Iterator[None]:
    """The deployment's switches and the instance's admin record, for the worker's assembly. ``admission``: an
    edition's ``McpToolAdmission`` the lane runs under; None is this build's open admission."""
    patch.setattr(edition_config, "mcp_tool_admission", admission, raising=False)
    runners_on(patch, ProcessStub(runner))
    patch.setenv("MCP_RUNNER_IMAGE", image)
    _ = hub_on(patch, ManualClock())
    record = instance(org)

    async def resolved(*_: object) -> dict[str, Any]:
        return dict(record)

    patch.setattr("app.edition_config.get_mcp_instance_resolved", resolved)
    yield


@contextlib.contextmanager
def echo_template() -> Iterator[None]:
    """The catalog's ``echo`` STDIO template (P5.10's test server), running the program the runner image carries:
    main's STDIO policy (#3887) runs a catalog instance's template, never the instance's stored command."""
    import pytest

    from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport

    template = MCPServerTemplate(
        type_id="echo",
        display_name="Echo",
        description="Test echo server",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_modes=[MCPAuthMode.API_TOKEN],
        command="python3",
        args=[SERVER_PATH],
        required_env=[SECRET_NAME],
    )
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._templates, "echo", template)  # pyright: ignore[reportPrivateUsage]
        yield


async def first_listing(config: FakeConfigurationService, org: Org) -> list[str]:
    """A discovery of the server as alice, before any run: the failure reasons its load recorded."""
    servers = [{"instanceId": INSTANCE_ID, "name": "EchoMCP", "displayName": "EchoMCP", "typeId": "echo"}]
    configs, *_ = await _resolve_mcp_server_configs(
        config,  # type: ignore[arg-type]
        servers,
        org_id=org.org_id,
        credential_lookup_id=org.user_id("alice"),
        executing_user_id=org.user_id("alice"),
        agent_id="admin-review",
        is_service_account=False,
        logger=LOG,  # type: ignore[arg-type]
    )
    context = make_context(
        config_service=config, org_id=org.org_id, user_id=org.user_id("alice"),
        user_info={"userId": org.user_id("alice"), "orgId": org.org_id},
        mcp_servers=servers, mcp_server_configs=configs, run_id="admin-review",
    )
    await MCPToolProvider().load_into(ToolRegistry(), context)
    reasons = [str(f.get("reason")) for f in context.mcp_tool_load_failures]
    await MCPSessionManager(context).aclose_all()
    return reasons


async def approve(config: FakeConfigurationService, org: Org, *, approved: tuple[str, ...], stale: tuple[str, ...]) -> list[str]:
    """For a lane under an admission that approves tools: P2.8's flow as an admin runs it. A discovery with nothing
    approved registers nothing and records what the server lists (with digests); the admin approves ``approved`` at
    those digests and ``stale`` at a digest that is no longer the live one (a tool the server changed since:
    disabled). Returns the first load's failure reasons."""
    reasons = await first_listing(config, org)
    drift = await config.get_config(DRIFT_PATH.format(INSTANCE_ID))
    listed = {t["name"]: t["digest"] for t in (drift or {}).get("tools") or []}
    tools = {name: {"digest": listed[name]} for name in approved}
    tools |= {name: {"digest": "sha256:" + "f" * 64} for name in stale}
    await config.set_config(MANIFEST_PATH.format(INSTANCE_ID), {"tools": tools})
    return reasons


__all__ = [
    "INSTANCE_ID",
    "SECRET_NAME",
    "SERVER_TOOLS",
    "SUITE",
    "approve",
    "build_runner_image",
    "echo_runner",
    "echo_template",
    "first_listing",
    "instance",
    "mcp_on",
]
