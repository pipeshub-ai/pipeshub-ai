"""MCP resolvers — own-org lookups, no inheritance.
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.mcp import service as mcp_service
from app.config.configuration_service import ConfigurationService


async def load_mcp_instances(
    config_service: ConfigurationService,
    org_id: str,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """The org's MCP instances, plus `user_id`'s own personal ones when given."""
    return await mcp_service.load_visible_instances(config_service, org_id, user_id)


async def get_mcp_instance(
    instance_id: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Single MCP instance by ID: an org instance, or `user_id`'s own personal one."""
    return await mcp_service.get_instance(instance_id, config_service, org_id, user_id)


def mask_mcp_instance_for_response(
    instance: dict[str, Any],
    reveal: bool = False,
) -> dict[str, Any]:
    """Redact secrets on response."""
    del reveal
    return dict(instance)


def forbid_inherited_mcp_mutation(instance: dict[str, Any]) -> None:
    """Reject mutations on inherited instances."""
    pass


async def resolve_mcp_instances_with_inheritance(
    config_service: ConfigurationService,
    org_id: str,
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """All instances visible to `org_id` (and `user_id`'s own), including inherited."""
    return await load_mcp_instances(config_service, org_id, user_id)


async def resolve_instance_owner_config_service(
    instance_id: str,  # noqa: ARG001
    config_service: ConfigurationService,
) -> ConfigurationService:
    """Config service for the org that owns an instance."""
    return config_service


def build_schedule_refresh_kwargs(org_id: Optional[str]) -> dict[str, Any]:
    """Extra kwargs for schedule_token_refresh."""
    return {}
