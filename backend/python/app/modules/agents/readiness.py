"""Whether a user can run an agent: every attached toolset must be configured and authenticated."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

TOOLSET_CONFIG_MISSING_CODE = "toolset_config_missing"


class AgentReadiness(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    can_send: bool
    missing_toolsets: list[str] = Field(default_factory=list)
    unauthenticated_toolsets: list[str] = Field(default_factory=list)
    is_service_account: bool = Field(default=False, exclude=True)
    # None when the agent has no named toolset to check.
    configured_toolsets: list[dict[str, Any]] | None = Field(default=None, exclude=True, repr=False)
    # SENSITIVE: holds credentials; never serialized.
    toolset_configs: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)

    @property
    def blocked_message(self) -> str | None:
        if self.can_send:
            return None
        problems = []
        if self.missing_toolsets:
            problems.append(f"not configured: {', '.join(repr(n) for n in self.missing_toolsets)}")
        if self.unauthenticated_toolsets:
            problems.append(f"not authenticated: {', '.join(repr(n) for n in self.unauthenticated_toolsets)}")
        joined = "; ".join(problems)
        if self.is_service_account:
            return (
                f"This service account agent requires the following actions to be configured — {joined}. "
                "Please configure the agent's action credentials in Agent Builder (key icon next to each action)."
            )
        return (
            f"This agent requires the following actions to be set up — {joined}. "
            "Please connect your actions in Workspace → Actions before using this agent."
        )


async def compute_agent_readiness(
    agent: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    config_service: ConfigurationService,
    agent_id: str | None = None,
    toolsets: Sequence[Mapping[str, Any]] | None = None,
    prefetched_auth: Mapping[str, dict[str, Any]] | None = None,
) -> AgentReadiness:
    """`toolsets` overrides the agent's own list (the chat route narrows it by the request's tool filter).

    Service-account agents keep their credentials under the agent key, everyone else under the user id.
    """
    from app.agents.constants.toolset_constants import get_toolset_config_path

    agent_toolsets = list(agent.get("toolsets", []) if toolsets is None else toolsets)
    is_service_account = bool(agent.get("isServiceAccount", False))
    executing_user_id = user["userId"]
    credential_lookup_id = (agent_id or agent.get("_key")) if is_service_account else executing_user_id
    named_toolsets = [t for t in agent_toolsets if t.get("instanceId") or t.get("name")]
    if not named_toolsets:
        return AgentReadiness(can_send=True, is_service_account=is_service_account)

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
        elif config:
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

    return AgentReadiness(
        can_send=not (missing or unauthenticated),
        missing_toolsets=missing,
        unauthenticated_toolsets=unauthenticated,
        is_service_account=is_service_account,
        configured_toolsets=configured,
        toolset_configs=toolset_configs,
    )
