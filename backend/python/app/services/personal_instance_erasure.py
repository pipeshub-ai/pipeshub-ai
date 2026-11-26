"""Erasing a deleted user's or org's personal instances (C-14 PRINCIPAL_DATA_STORES ``personalInstances``).

Personal toolset instances (main #4324) and personal MCP servers live in the config store, one key per
instance under their owner, with sign-ins, tool rules and owner pointers beside them. Node's user and org
deletion call this through the connectors service (which runs the token refreshers, so their tasks stop
here too) before the principal is marked deleted, and again until nothing is left: every step is
idempotent, and a key still there afterwards fails the call rather than answering "erased".

The Kafka user and org deletion events still remove the same data (main's ``_remove_personal_toolsets``
and ``_remove_mcp_data``); they are best effort and run after the principal is gone, so they are not what
erasure relies on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents import toolset_store
from app.agents.constants.mcp_server_constants import (
    get_mcp_org_user_instances_prefix,
    get_mcp_user_instances_prefix,
)
from app.agents.mcp import lifecycle as mcp_lifecycle
from app.agents.toolset_store import PersonalDataRemainsError

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

__all__ = ["PersonalDataRemainsError", "erase_personal_instances"]


async def erase_personal_instances(
    config_service: ConfigurationService, org_id: str, user_id: str | None,
) -> dict[str, int]:
    """`user_id`'s personal toolset instances and MCP servers in `org_id` with everything stored for them,
    and their sign-ins to the org's instances; with `user_id` None, every member's in the org.

    Raises:
        PersonalDataRemainsError: a key of the principal is still there afterwards; call again.
        ValueError: the org or user id would widen the erasure to someone else's keys.
    """
    if user_id is None:
        toolsets = await toolset_store.erase_org_toolset_data(config_service, org_id)
        mcp = await mcp_lifecycle.remove_org_mcp_data(config_service, org_id)
        mcp_prefix = get_mcp_org_user_instances_prefix(org_id)
    else:
        toolsets = await toolset_store.erase_user_toolset_data(config_service, org_id, user_id)
        mcp = await mcp_lifecycle.remove_user_mcp_data(config_service, org_id, user_id)
        mcp_prefix = get_mcp_user_instances_prefix(org_id, user_id)
    # MCP's removal is best effort (it logs a failed delete); the listing here is not.
    left = [key for key in await config_service.list_keys_in_directory(mcp_prefix) if key.startswith(mcp_prefix)]
    if left:
        raise PersonalDataRemainsError(left)
    return {**toolsets, "mcpKeys": len(mcp)}
