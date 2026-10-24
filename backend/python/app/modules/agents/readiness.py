"""Which of an agent's toolsets and MCP servers the caller can use right now.

A normal agent runs with the caller's own credentials and leaves out what they
have not connected; the run tells the model and the client what is missing. A
service-account agent runs with its own credentials, so a missing one stops the
run until an admin sets it up.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

TOOLSET_CONFIG_MISSING_CODE = "toolset_config_missing"
MCP_SERVER_CONFIG_MISSING_CODE = "mcp_server_config_missing"
# Non-fatal custom stream event: the run goes on without these tools.
TOOLS_UNAVAILABLE_EVENT = "tools_unavailable"

# Names come from admin-set display names; keep a prompt line short and on one line.
_MAX_PROMPT_NAME_LEN = 80


class UnavailableTool(BaseModel):
    """One attached toolset or MCP server the run cannot use. Holds no credentials."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    kind: Literal["toolset", "mcp"]
    instance_id: str
    display_name: str
    name: str | None = None
    problem: Literal["not_configured", "not_authenticated", "not_found"]
    auth_mode: str | None = None
    # One org credential that only an admin can enter (`useAdminAuth`).
    shared_credential: bool = False
    # The caller can fix it themselves: never for a service-account agent, a
    # shared credential, or a server that no longer exists.
    user_can_connect: bool = False

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    def legacy_mcp_server(self) -> dict[str, Any]:
        """The `details.servers[]` entry older clients read for `mcp_server_config_missing`."""
        if self.problem == "not_found":
            return {"instanceId": self.instance_id, "name": self.display_name, "problem": "not_found"}
        return {
            "instanceId": self.instance_id,
            "name": self.display_name,
            "problem": "not_connected",
            "authMode": self.auth_mode,
            "sharedCredential": self.shared_credential,
        }


class AgentReadiness(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # False only for a service-account agent missing a credential: every other
    # agent runs with the tools the caller has.
    can_send: bool
    missing_toolsets: list[str] = Field(default_factory=list)
    unauthenticated_toolsets: list[str] = Field(default_factory=list)
    is_service_account: bool = Field(default=False, alias="serviceAccount")
    unavailable_tools: list[UnavailableTool] = Field(default_factory=list)
    # None when the agent has no named toolset to check.
    configured_toolsets: list[dict[str, Any]] | None = Field(default=None, exclude=True, repr=False)
    # SENSITIVE: holds credentials; never serialized.
    toolset_configs: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    # None when MCP servers were not checked.
    configured_mcp_servers: list[dict[str, Any]] | None = Field(default=None, exclude=True, repr=False)
    # SENSITIVE: instance definitions and resolved credentials, keyed by instanceId.
    mcp_server_configs: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True, repr=False)

    @property
    def blocked_code(self) -> str:
        if any(item.kind == "toolset" for item in self.unavailable_tools):
            return TOOLSET_CONFIG_MISSING_CODE
        return MCP_SERVER_CONFIG_MISSING_CODE

    @property
    def blocked_message(self) -> str | None:
        if self.can_send:
            return None
        problems = ", ".join(f"'{item.display_name}' ({_problem_words(item)})" for item in self.unavailable_tools)
        return (
            f"This service account agent uses its own connections, and these are not set up: {problems}. "
            "Ask an admin to connect them in Agent Builder."
        )

    def blocked_details(self, agent_id: str | None) -> dict[str, Any]:
        return {
            "agentId": agent_id,
            "serviceAccount": self.is_service_account,
            "items": [item.wire() for item in self.unavailable_tools],
            "servers": [item.legacy_mcp_server() for item in self.unavailable_tools if item.kind == "mcp"],
        }

    def unavailable_event(self, agent_id: str | None) -> dict[str, Any]:
        return {
            "agentId": agent_id,
            "serviceAccount": self.is_service_account,
            "items": [item.wire() for item in self.unavailable_tools],
        }


def _problem_words(item: UnavailableTool) -> str:
    if item.problem == "not_found":
        return "removed"
    if item.problem == "not_configured":
        return "not configured"
    return "not connected"


def _prompt_name(raw: object) -> str:
    text = " ".join(str(raw or "").split())
    return text[:_MAX_PROMPT_NAME_LEN] or "unnamed tool"


def unavailable_tools_note(items: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The system-prompt lines naming the attached tools this run left out, or None."""
    if not items:
        return None
    by_user = [_prompt_name(i.get("displayName")) for i in items if i.get("userCanConnect")]
    by_admin = [_prompt_name(i.get("displayName")) for i in items if not i.get("userCanConnect")]
    lines = ["## Tools that are not connected"]
    if by_user:
        lines.append(f"Unavailable until the user connects them: {', '.join(by_user)}.")
    if by_admin:
        lines.append(f"Unavailable until an admin sets them up: {', '.join(by_admin)}.")
    lines.append(
        "If the request needs one of them, say so plainly and name it; don't pretend to use it or "
        "invent its results. If the request does not need them, don't mention them."
    )
    return "\n".join(lines)


async def _toolset_readiness(
    named_toolsets: Sequence[Mapping[str, Any]],
    *,
    config_service: ConfigurationService,
    credential_lookup_id: str,
    executing_user_id: str,
    is_service_account: bool,
    prefetched_auth: Mapping[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], list[str], list[UnavailableTool]]:
    from app.agents.constants.toolset_constants import get_toolset_config_path

    async def _fetch(toolset: Mapping[str, Any]) -> tuple[Mapping[str, Any], Any]:
        lookup_key = toolset.get("instanceId")
        # `get_assistant_agent` already read the executing user's config for each instance.
        prefetched = (
            prefetched_auth.get(lookup_key)
            if prefetched_auth is not None and credential_lookup_id == executing_user_id
            else None
        )
        if prefetched is not None:
            return toolset, prefetched
        try:
            return toolset, await config_service.get_config(get_toolset_config_path(lookup_key, credential_lookup_id))
        except Exception as exc:
            logger.warning(
                "Failed to load config for toolset '%s' (lookup_key='%s'): %s", toolset.get("name", ""), lookup_key, exc,
            )
            return toolset, None

    results = await asyncio.gather(*[_fetch(t) for t in named_toolsets])

    configured: list[dict[str, Any]] = []
    toolset_configs: dict[str, Any] = {}
    missing: list[str] = []
    unauthenticated: list[str] = []
    items: list[UnavailableTool] = []
    cred_owner = f"agent '{credential_lookup_id}'" if is_service_account else f"user '{executing_user_id}'"
    for toolset, config in results:
        instance_id = toolset.get("instanceId")
        toolset_name = toolset.get("name", "")
        display_name = (
            toolset.get("instanceName") or toolset.get("displayName") or toolset_name.replace("_", " ").title()
        )
        if config and config.get("isAuthenticated", False):
            toolset_configs[instance_id] = config
            configured.append(toolset)
            continue
        if config:
            unauthenticated.append(display_name)
            logger.warning(
                "Toolset '%s' (instance='%s') is configured but not authenticated for %s. "
                "Auth flow needs to be completed.",
                toolset_name, instance_id, cred_owner,
            )
        else:
            missing.append(display_name)
            logger.warning(
                "Toolset config not found for %s / toolset '%s' (instance='%s'). Credentials need to be configured.",
                cred_owner, toolset_name, instance_id,
            )
        items.append(UnavailableTool(
            kind="toolset",
            instance_id=str(instance_id or toolset_name),
            display_name=display_name,
            name=toolset_name or None,
            problem="not_authenticated" if config else "not_configured",
            user_can_connect=not is_service_account,
        ))
    return configured, toolset_configs, missing, unauthenticated, items


async def _mcp_readiness(
    named_servers: Sequence[Mapping[str, Any]],
    *,
    config_service: ConfigurationService,
    org_id: str,
    credential_lookup_id: str,
    executing_user_id: str,
    is_service_account: bool,
    prefetched_mcp: Mapping[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[UnavailableTool]]:
    """An MCP server also needs its org-level instance (transport/url/authMode), which the graph
    node never stores, so each server is read in two steps: the instance, then its credential."""
    from app.agents.mcp import service as mcp_service
    from app.edition_config import get_mcp_instance_resolved

    async def _fetch(server: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any] | None, Any]:
        instance_id = server["instanceId"]
        # Read once already for the assistant, always as the executing user.
        if not is_service_account and prefetched_mcp and (prefetched := prefetched_mcp.get(instance_id)):
            return server, prefetched["instance"], prefetched["auth"]
        try:
            instance = await get_mcp_instance_resolved(
                instance_id, config_service, org_id, None if is_service_account else executing_user_id,
            )
            if not instance:
                return server, None, None
            effective_auth = await mcp_service.resolve_effective_user_auth(
                instance, credential_lookup_id, config_service,
            )
            return server, instance, effective_auth
        except Exception as exc:
            logger.warning("Failed to load MCP server config for instance '%s': %s", instance_id, exc)
            return server, None, None

    results = await asyncio.gather(*[_fetch(s) for s in named_servers])

    configured: list[dict[str, Any]] = []
    configs: dict[str, dict[str, Any]] = {}
    items: list[UnavailableTool] = []
    cred_owner = f"agent '{credential_lookup_id}'" if is_service_account else f"user '{executing_user_id}'"
    for server, instance, effective_auth in results:
        instance_id = server["instanceId"]
        display_name = server.get("displayName") or server.get("name") or instance_id
        if instance is None:
            logger.warning("MCP server instance '%s' not found.", instance_id)
            items.append(UnavailableTool(
                kind="mcp", instance_id=instance_id, display_name=display_name,
                name=server.get("name"), problem="not_found",
            ))
            continue
        if mcp_service.is_effective_auth_authenticated(effective_auth):
            configs[instance_id] = {"instance": instance, "auth": effective_auth or {}, "ownerId": credential_lookup_id}
            configured.append(dict(server))
            continue
        shared = mcp_service.uses_shared_credential(instance)
        logger.warning(
            "MCP server '%s' (instance='%s') is not authenticated for %s.", display_name, instance_id, cred_owner,
        )
        items.append(UnavailableTool(
            kind="mcp", instance_id=instance_id, display_name=display_name, name=server.get("name"),
            problem="not_authenticated", auth_mode=instance.get("authMode"), shared_credential=shared,
            user_can_connect=not is_service_account and not shared,
        ))
    return configured, configs, items


async def compute_agent_readiness(
    agent: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    config_service: ConfigurationService,
    agent_id: str | None = None,
    toolsets: Sequence[Mapping[str, Any]] | None = None,
    prefetched_auth: Mapping[str, dict[str, Any]] | None = None,
    mcp_servers: Sequence[Mapping[str, Any]] | None = None,
    prefetched_mcp: Mapping[str, dict[str, Any]] | None = None,
) -> AgentReadiness:
    """`toolsets` overrides the agent's own list (the chat route narrows it by the request's tool filter).
    `mcp_servers` are checked only when given (None skips MCP).

    Service-account agents keep their credentials under the agent key, everyone else under the user id.
    Nothing here ever reads another person's credentials.
    """
    agent_toolsets = list(agent.get("toolsets", []) if toolsets is None else toolsets)
    is_service_account = bool(agent.get("isServiceAccount", False))
    executing_user_id = user["userId"]
    credential_lookup_id = (agent_id or agent.get("_key")) if is_service_account else executing_user_id
    named_toolsets = [t for t in agent_toolsets if t.get("instanceId") or t.get("name")]
    named_servers = [m for m in (mcp_servers or []) if m.get("instanceId")]

    configured_toolsets: list[dict[str, Any]] | None = None
    toolset_configs: dict[str, Any] = {}
    missing: list[str] = []
    unauthenticated: list[str] = []
    items: list[UnavailableTool] = []
    if named_toolsets:
        configured_toolsets, toolset_configs, missing, unauthenticated, toolset_items = await _toolset_readiness(
            named_toolsets,
            config_service=config_service,
            credential_lookup_id=credential_lookup_id,
            executing_user_id=executing_user_id,
            is_service_account=is_service_account,
            prefetched_auth=prefetched_auth,
        )
        items.extend(toolset_items)

    configured_mcp: list[dict[str, Any]] | None = None
    mcp_configs: dict[str, dict[str, Any]] = {}
    if mcp_servers is not None:
        configured_mcp, mcp_configs, mcp_items = await _mcp_readiness(
            named_servers,
            config_service=config_service,
            org_id=str(user.get("orgId") or ""),
            credential_lookup_id=credential_lookup_id,
            executing_user_id=executing_user_id,
            is_service_account=is_service_account,
            prefetched_mcp=prefetched_mcp,
        ) if named_servers else (None, {}, [])
        items.extend(mcp_items)

    return AgentReadiness(
        can_send=not (is_service_account and items),
        missing_toolsets=missing,
        unauthenticated_toolsets=unauthenticated,
        is_service_account=is_service_account,
        unavailable_tools=items,
        configured_toolsets=configured_toolsets,
        toolset_configs=toolset_configs,
        configured_mcp_servers=configured_mcp,
        mcp_server_configs=mcp_configs,
    )
