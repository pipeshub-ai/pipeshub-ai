"""What a STDIO MCP server's runner may reach and which image it runs in (P5.10).

A server reaches the hosts it declares, a catalog server's in its catalog entry and a custom server's in its setup form
with its command (``declaredEgressHosts``), and runs in the deployment's runner image (``MCP_RUNNER_IMAGE``). An edition extension may set per-server
values over these through ``app.edition_config.mcp_runner_settings``; without one, the declaration is the setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.agents.mcp.models import MCPServerTemplate

DECLARED_EGRESS_HOSTS = "declaredEgressHosts"
MAX_EGRESS_HOSTS = 32


@dataclass(frozen=True, slots=True)
class RunnerFields:
    egress_hosts: tuple[str, ...] = ()
    # None: the deployment's runner image.
    runner_image: str | None = None


class McpRunnerSettings(Protocol):
    def fields(self, instance: Mapping[str, object], declared: RunnerFields) -> RunnerFields:
        """The runner fields of the stored ``instance``, given what its setup declared."""
        ...


def declared_fields(instance: Mapping[str, object], template: MCPServerTemplate | None = None) -> RunnerFields:
    if template is not None:
        return RunnerFields(egress_hosts=tuple(template.egress_hosts))
    hosts = instance.get(DECLARED_EGRESS_HOSTS)
    if not isinstance(hosts, list):
        return RunnerFields()
    return RunnerFields(egress_hosts=tuple(h for h in hosts if isinstance(h, str)))


def runner_fields(instance: Mapping[str, object], template: MCPServerTemplate | None = None) -> RunnerFields:
    """``template``: the catalog entry of a catalog instance. Read at call time so an edition extension that sets
    ``mcp_runner_settings`` is honoured."""
    from app import edition_config

    declared = declared_fields(instance, template)
    settings: McpRunnerSettings | None = getattr(edition_config, "mcp_runner_settings", None)
    return declared if settings is None else settings.fields(instance, declared)


__all__ = [
    "DECLARED_EGRESS_HOSTS",
    "MAX_EGRESS_HOSTS",
    "McpRunnerSettings",
    "RunnerFields",
    "declared_fields",
    "runner_fields",
]
