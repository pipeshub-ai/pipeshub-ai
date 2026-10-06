"""Who may decide what an STDIO MCP server runs on the PipesHub host.

A STDIO server is a process spawned by the connectors (tool discovery) and query (agent
loop) services, as the service user. Org admins pick servers; the deployment operator owns
the host. So:

- catalog servers always run the registry's command/args, never a stored or submitted one;
- custom STDIO servers run only when the operator sets ``MCP_ALLOW_CUSTOM_STDIO=true`` in
  the process environment (compose ``.env`` / Helm values), which an org admin cannot reach;
- env var names handed to the child are restricted so a permitted command can't be turned
  into a different one (``NODE_OPTIONS``, ``LD_PRELOAD``, ``PATH``, ...).
"""
import os
import re
from collections.abc import Iterable
from typing import Any, Optional

from app.agents.mcp.models import MCPServerConfig, MCPTransport
from app.agents.mcp.registry import MCPRegistry, get_mcp_registry

CUSTOM_STDIO_ENV = "MCP_ALLOW_CUSTOM_STDIO"
CUSTOM_STDIO_DISABLED_REASON = "custom_stdio_disabled"
CUSTOM_STDIO_DISABLED_MESSAGE = (
    "Custom STDIO MCP servers are disabled on this deployment because they run a command on the "
    f"PipesHub server. Use a remote (HTTP) MCP server, or ask the operator to set {CUSTOM_STDIO_ENV}=true."
)

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_DENIED_ENV_NAMES = frozenset({
    "PATH", "HOME", "SHELL", "ENV", "BASH_ENV", "IFS", "CDPATH", "PS4", "PROMPT_COMMAND",
    "CLASSPATH", "JDK_JAVA_OPTIONS",
})
_DENIED_ENV_PREFIXES = (
    "LD_", "DYLD_", "NODE_", "NPM_CONFIG_", "COREPACK_", "DENO_", "BUN_", "PYTHON", "UV_", "PIP_",
    "GIT_", "PERL", "RUBY", "JAVA_", "GCONV_",
)


class StdioPolicyError(Exception):
    """An STDIO launch or instance config is not allowed by the deployment's policy."""


def custom_stdio_allowed() -> bool:
    # Process env only: ConfigurationService keys are writable by org admins.
    return os.getenv(CUSTOM_STDIO_ENV, "false").strip().lower() == "true"


def is_allowed_env_name(name: str) -> bool:
    return (
        bool(_ENV_NAME_RE.match(name))
        and name not in _DENIED_ENV_NAMES
        and not name.startswith(_DENIED_ENV_PREFIXES)
    )


def rejected_env_names(names: Iterable[str]) -> list[str]:
    return sorted({str(name) for name in names if not is_allowed_env_name(str(name))})


def is_custom_stdio(type_id: Optional[str], transport: Any) -> bool:
    transport_value = transport.value if isinstance(transport, MCPTransport) else transport
    return not type_id and transport_value == MCPTransport.STDIO.value


def instance_disabled_reason(instance: dict[str, Any]) -> Optional[str]:
    """Why a stored instance will not run on this deployment, for list/get responses."""
    if is_custom_stdio(instance.get("typeId"), instance.get("transport")) and not custom_stdio_allowed():
        return CUSTOM_STDIO_DISABLED_REASON
    return None


def resolve_stdio_launch(
    config: MCPServerConfig,
    registry: Optional[MCPRegistry] = None,
) -> tuple[str, list[str]]:
    """The (command, args) to spawn for an STDIO instance, or raise StdioPolicyError.

    Stored ``command``/``args`` are ignored for catalog instances, so records saved before
    this policy (or edited directly in the KV store) cannot override the template.
    """
    if config.type_id:
        if registry is None:
            registry = get_mcp_registry()
            registry.auto_discover_templates()
        template = registry.get_template(config.type_id)
        if template is None or template.transport != MCPTransport.STDIO or not template.command:
            raise StdioPolicyError(
                f"MCP instance {config.id} references catalog server '{config.type_id}', "
                "which is not a STDIO server in this release's catalog."
            )
        return template.command, list(template.args)

    if not custom_stdio_allowed():
        raise StdioPolicyError(CUSTOM_STDIO_DISABLED_MESSAGE)
    if not config.command:
        raise StdioPolicyError(f"MCP instance {config.id} is STDIO but has no command configured")
    return config.command, list(config.args or [])
