"""MCP instance lookups scoped to the caller's org: the only way callers resolve an instance.

An instance lives at ``/services/mcp/instances/{instanceId}``, a key that names no org, so a store
holding several orgs answers any org's id. Every lookup here loads through the edition resolver and
keeps an instance only when the edition's ``mcp_instance_visible_to_org`` admits it for ``org_id``:
the community edition admits the instance's own org, an edition with org inheritance also admits
what the org inherits. ``org_id`` must come from the authenticated caller or the server-side run,
never from a client-supplied value. Another org's instance resolves like a missing one.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService


async def get_instance_for_org(
    instance_id: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """An org instance, or ``user_id``'s own personal one."""
    from app import edition_config

    instance = await edition_config.get_mcp_instance_resolved(instance_id, config_service, org_id, user_id)
    if not instance or not await edition_config.mcp_instance_visible_to_org(instance, org_id, config_service):
        return None
    return instance


async def list_instances_for_org(
    config_service: ConfigurationService,
    org_id: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """The org's instances, and ``user_id``'s own personal ones when given."""
    from app import edition_config

    instances = await edition_config.resolve_mcp_instances_with_inheritance(config_service, org_id, user_id)
    return [
        instance
        for instance in instances
        if await edition_config.mcp_instance_visible_to_org(instance, org_id, config_service)
    ]
