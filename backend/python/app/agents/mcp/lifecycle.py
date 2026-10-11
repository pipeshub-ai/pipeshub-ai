"""Removing MCP data when what it belongs to goes away: a server instance, a user, an agent or
an organization.

Every function is best-effort: a delete that fails is logged and the rest still run, because the
caller has usually already committed the change that made the data stale (the user, agent or org
is gone) and has nothing to roll back. Each returns the keys it removed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.constants.mcp_server_constants import (
    MCP_ROOT,
    get_mcp_instance_credentials_prefix,
    get_mcp_instance_path,
    get_mcp_oauth_client_config_path,
    get_mcp_oauth_states_prefix,
    get_mcp_org_user_instances_prefix,
    get_mcp_shared_dcr_client_path,
    get_mcp_tool_policy_path,
    get_mcp_user_instance_path,
    get_mcp_user_instances_prefix,
)
from app.agents.mcp import service as mcp_service

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.token_service.mcp_token_refresh_service import (
        MCPTokenRefreshService,
    )

logger = logging.getLogger(__name__)

_CREDENTIALS_PREFIX = f"{MCP_ROOT}/credentials/"

__all__ = [
    "credential_has_instance",
    "delete_instance_data",
    "purge_instance_credentials",
    "remove_org_mcp_data",
    "remove_owner_credentials",
    "remove_user_mcp_data",
]


def _refresh_service() -> "MCPTokenRefreshService | None":
    """The connectors service's token refresher, when this process runs one."""
    try:
        from app.connectors.core.base.token_service.startup_service import (
            startup_service,
        )

        return startup_service.get_mcp_token_refresh_service()
    except Exception:
        return None


async def _delete(config_service: ConfigurationService, key: str, removed: list[str]) -> None:
    try:
        await config_service.delete_config(key)
        removed.append(key)
    except Exception as e:
        logger.warning(f"Could not delete MCP key {key}: {e}")


async def _list(config_service: ConfigurationService, prefix: str) -> list[str]:
    try:
        return list(await config_service.list_keys_in_directory(prefix))
    except Exception as e:
        logger.warning(f"Could not list MCP keys to remove: {e}")
        return []


async def purge_instance_credentials(config_service: ConfigurationService, instance_id: str) -> list[str]:
    """Every user's and agent's credentials for the instance, its shared DCR client and any
    authorization still in flight. The admin's static OAuth app config is kept."""
    refresh_service = _refresh_service()
    if refresh_service:
        refresh_service.cancel_refresh_tasks_for_instance(instance_id)

    removed: list[str] = []
    for key in await _list(config_service, get_mcp_instance_credentials_prefix(instance_id)):
        await _delete(config_service, key, removed)
    await _delete(config_service, get_mcp_shared_dcr_client_path(instance_id), removed)

    for key in await _list(config_service, get_mcp_oauth_states_prefix()):
        state = await config_service.get_config(key, default=None)
        if isinstance(state, dict) and state.get("instanceId") == instance_id:
            await _delete(config_service, key, removed)
    return removed


async def delete_instance_data(config_service: ConfigurationService, instance: dict[str, Any]) -> list[str]:
    """The instance record and everything stored for it."""
    removed: list[str] = []
    await _delete(config_service, mcp_service.instance_record_path(instance), removed)
    removed += await purge_instance_credentials(config_service, instance["_id"])
    await _delete(config_service, get_mcp_oauth_client_config_path(instance["_id"]), removed)
    await _delete(config_service, get_mcp_tool_policy_path(instance["_id"]), removed)
    return removed


async def remove_owner_credentials(
    config_service: ConfigurationService, org_id: str, owner_id: str,
) -> list[str]:
    """`owner_id`'s credentials (a user's or a service-account agent's) on `org_id`'s servers,
    including the keys nested under them (a legacy per-owner DCR client). One listing of the
    credentials directory, filtered to this org's instances, so another org's data is never
    touched even if an id repeated."""
    instance_ids = {i["_id"] for i in await mcp_service.load_org_instances(config_service, org_id)}
    instance_ids |= {
        i["_id"] for i in await mcp_service.load_user_instances(config_service, org_id, owner_id)
    }
    refresh_service = _refresh_service()
    removed: list[str] = []
    for key in await _list(config_service, _CREDENTIALS_PREFIX):
        # /services/mcp/credentials/{instanceId}/{ownerId}[/{sub}]
        parts = key[len(_CREDENTIALS_PREFIX):].strip("/").split("/")
        if len(parts) < 2 or parts[0] not in instance_ids or parts[1] != owner_id:
            continue
        if refresh_service and len(parts) == 2:
            refresh_service.cancel_refresh_task(key)
        await _delete(config_service, key, removed)
    return removed


async def remove_user_mcp_data(config_service: ConfigurationService, org_id: str, user_id: str) -> list[str]:
    """Everything of a deleted user's: their personal servers and their credentials on the org's."""
    removed: list[str] = []
    for instance in await mcp_service.load_user_instances(config_service, org_id, user_id):
        removed += await delete_instance_data(config_service, instance)
    # Personal servers whose record was unreadable still leave keys under the user's prefix.
    for key in await _list(config_service, get_mcp_user_instances_prefix(org_id, user_id)):
        await _delete(config_service, key, removed)
    removed += await remove_owner_credentials(config_service, org_id, user_id)
    return removed


async def credential_has_instance(config_service: ConfigurationService, credential_path: str) -> bool | None:
    """Whether the server a credential at `/services/mcp/credentials/{instanceId}/{ownerId}`
    belongs to still exists: True, False, or None when that can't be told (a store error, or a
    record too old to name its org). Only a definite False may lead to deleting anything."""
    parts = credential_path[len(_CREDENTIALS_PREFIX):].strip("/").split("/") if credential_path.startswith(_CREDENTIALS_PREFIX) else []
    if len(parts) != 2:
        return None
    instance_id, owner_id = parts
    try:
        if isinstance(await config_service.get_config(get_mcp_instance_path(instance_id), raise_on_error=True), dict):
            return True
        record = await config_service.get_config(credential_path, raise_on_error=True)
        org_id = record.get("orgId") if isinstance(record, dict) else None
        if not org_id:
            return None
        personal = await config_service.get_config(
            get_mcp_user_instance_path(org_id, owner_id, instance_id), raise_on_error=True,
        )
    except Exception as e:
        logger.debug(f"Could not tell whether the server of {credential_path} exists: {e}")
        return None
    return isinstance(personal, dict)


async def remove_org_mcp_data(config_service: ConfigurationService, org_id: str) -> list[str]:
    """Every MCP server of a deleted organization, org-wide and personal, with all their data."""
    removed: list[str] = []
    for instance in await mcp_service.load_org_instances(config_service, org_id):
        removed += await delete_instance_data(config_service, instance)
    for instance in await mcp_service.load_personal_instances_for_admin(config_service, org_id):
        removed += await delete_instance_data(config_service, instance)
    for key in await _list(config_service, get_mcp_org_user_instances_prefix(org_id)):
        await _delete(config_service, key, removed)
    return removed
