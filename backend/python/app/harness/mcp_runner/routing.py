"""Which STDIO MCP instances run in runners, and the runner request for one (P5.10, D23).

With ``MCP_STDIO_RUNNERS`` on, every STDIO instance PipesHub connects to (agent
discovery and sessions, the admin "discover tools" routes) is served by the
process's ``MCPRunner``; with it off nothing here is called. The runner is keyed
to the connection's owner, and the secrets are the env the credential resolved
to at this boundary; they are not kept here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agents.mcp.discovery import DEFAULT_ENV_VAR_NAME, build_auth_env_and_headers
from app.agents.mcp.models import MCPTransport
from app.agents.mcp.stdio_policy import StdioPolicyError, resolve_stdio_launch
from app.harness.mcp_runner.process import process_mcp_runner
from app.harness.mcp_runner.settings import runner_settings, runners_enabled
from app.harness.mcp_runner.transport import RunnerTarget
from harness_core.logs import HarnessLog
from harness_core.sandbox.mcp_runner.errors import MCPRunnerRefused
from harness_core.sandbox.mcp_runner.policy import check_request, parse_egress_host
from harness_core.sandbox.mcp_runner.types import (
    MCPRunnerSpec,
    RunnerKey,
    RunnerRequest,
    RunnerSecrets,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agents.mcp.models import MCPServerConfig
    from app.harness.mcp_runner.settings import RunnerSettings
    from harness_core.sandbox.mcp_runner.ports import MCPRunner

_LOG = HarnessLog.named("app.harness.mcp_runner")


def runs_in_runner(config: MCPServerConfig) -> bool:
    return config.transport == MCPTransport.STDIO and runners_enabled()


def declared_env_names(config: MCPServerConfig) -> frozenset[str]:
    """The names ``build_auth_env_and_headers`` may set for this instance: its declared env, else ``API_TOKEN``."""
    names = {*config.required_env, *config.optional_env}
    if not config.required_env:
        names.add(DEFAULT_ENV_VAR_NAME)
    return frozenset(names)


def runner_spec_for(config: MCPServerConfig, settings: RunnerSettings) -> MCPRunnerSpec:
    """Raises ``StdioPolicyError`` for a launch main's STDIO policy refuses on the host too (#3887): a
    catalog instance runs its template's command, a custom one only where the operator allows custom STDIO."""
    command, args = resolve_stdio_launch(config)
    return MCPRunnerSpec(
        server_id=config.id,
        image=config.runner_image or settings.image,
        command=command,
        args=tuple(args),
        env_names=declared_env_names(config),
        egress=tuple(parse_egress_host(host) for host in config.egress_hosts),
        isolation_floor=settings.isolation_floor,
    )


async def runner_target(
    config: MCPServerConfig,
    env: Mapping[str, str],
    *,
    tenant: str,
    owner: str,
    principal: str,
    runner: MCPRunner | None = None,
) -> RunnerTarget:
    """The runner request for ``config`` as ``owner``'s connection; ``principal`` owns the resolved secrets.

    Raises ``MCPRunnerRefused`` (``RunnerUnavailable`` when no broker answers) rather than falling back.
    """
    settings = runner_settings()
    try:
        request = RunnerRequest(
            key=RunnerKey(tenant, config.id, owner),
            spec=runner_spec_for(config, settings),
            principal=principal,
            secrets=RunnerSecrets(env),
        )
        # Refused here, before the MCP client exists, so the caller sees the reason rather than a wrapped error.
        check_request(request)
    except StdioPolicyError as policy:
        _LOG.warning("mcp_runner.refused", orgId=tenant, mcpServer=config.id, reasonCode="stdio_launch_refused")
        raise MCPRunnerRefused(str(policy), reason="stdio_launch_refused") from policy
    except MCPRunnerRefused as refused:
        _LOG.warning("mcp_runner.refused", orgId=tenant, mcpServer=config.id, reasonCode=refused.reason_code)
        raise
    return RunnerTarget(runner or await process_mcp_runner.get(settings), request)


async def stdio_target(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    principal: str | None = None,
) -> RunnerTarget | None:
    """The runner for a STDIO instance when runners are on; ``None`` means today's path (not STDIO, or off).

    ``credentials`` is the discovery-shaped record resolved at this boundary; its env goes to the runner only.
    """
    if not runs_in_runner(config):
        return None
    env, _ = build_auth_env_and_headers(config, dict(credentials))
    return await runner_target(config, env, tenant=tenant, owner=owner, principal=principal or owner)


__all__ = ["declared_env_names", "runner_spec_for", "runner_target", "runs_in_runner", "stdio_target"]
