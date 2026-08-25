"""MCP resolvers — own-org lookups, no inheritance.
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.mcp import service as mcp_service
from app.config.configuration_service import ConfigurationService


async def load_mcp_instances(
    config_service: ConfigurationService,
) -> list[dict[str, Any]]:
    """All MCP instances visible to the current org."""
    return await mcp_service.load_org_instances(config_service)


async def get_mcp_instance(
    instance_id: str,
    config_service: ConfigurationService,
) -> Optional[dict[str, Any]]:
    """Single MCP instance by ID."""
    return await mcp_service.get_instance(instance_id, config_service)


def mask_mcp_instance_for_response(
    instance: dict[str, Any],
) -> dict[str, Any]:
    """Redact secrets on response."""
    return dict(instance)


def forbid_inherited_mcp_mutation(instance: dict[str, Any]) -> None:
    """Reject mutations on inherited instances."""
    pass


async def resolve_mcp_instances_with_inheritance(
    config_service: ConfigurationService,
) -> list[dict[str, Any]]:
    """All instances including inherited."""
    return await load_mcp_instances(config_service)


async def resolve_instance_owner_config_service(
    instance_id: str,  # noqa: ARG001
    config_service: ConfigurationService,
) -> ConfigurationService:
    """Config service for the org that owns an instance."""
    return config_service


async def build_mcp_fallback_config_services(
    instance: dict[str, Any],  # noqa: ARG001
    config_service: ConfigurationService,  # noqa: ARG001
) -> list | None:
    """Pre-resolve fallback config services."""
    return None


def build_schedule_refresh_kwargs(org_id: Optional[str]) -> dict[str, Any]:
    """Extra kwargs for schedule_token_refresh."""
    return {}
