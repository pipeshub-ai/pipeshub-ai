"""More scopes after a 403: a server that refuses a request with `insufficient_scope` names the
scopes it needs (RFC 6750 §3.1; the MCP spec's step-up authorization).

They're kept under the sign-in's own key, not in its credential record, which the token refresh
rewrites under its lock. The next authorization asks for them on top of what the sign-in has,
and the callback clears them.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agents.constants.mcp_server_constants import get_mcp_step_up_scopes_path
from app.agents.mcp.errors import insufficient_scope
from app.agents.mcp.models import MCPAuthMode
from app.agents.mcp.www_authenticate import MAX_SCOPES, challenge_scopes
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)


async def remember_needed_scopes(
    config_service: ConfigurationService, instance: dict[str, Any], owner_id: str, exc: BaseException,
) -> list[str]:
    """The scopes a 403 `insufficient_scope` in `exc` names, added to those `owner_id`'s next
    sign-in asks for. [] when it isn't one, names none, or the server doesn't use OAuth, since
    signing in again can't fix that. A failed save is only logged."""
    if instance.get("authMode") != MCPAuthMode.OAUTH.value:
        return []
    refused = insufficient_scope(exc)
    scopes = challenge_scopes(refused.challenge) if refused is not None else []
    if not scopes:
        return []
    try:
        saved = await step_up_scopes(config_service, instance["_id"], owner_id)
        merged = list(dict.fromkeys([*saved, *scopes]))[:MAX_SCOPES]
        if merged != saved:
            await config_service.set_config(
                get_mcp_step_up_scopes_path(instance["_id"], owner_id),
                {"scopes": merged, "updatedAt": get_epoch_timestamp_in_ms()},
            )
    except Exception:
        logger.warning("Couldn't save the scopes an MCP server asked for", exc_info=True)
    return scopes


async def step_up_scopes(config_service: ConfigurationService, instance_id: str, owner_id: str) -> list[str]:
    record = await config_service.get_config(get_mcp_step_up_scopes_path(instance_id, owner_id), default=None, use_cache=False)
    scopes = record.get("scopes") if isinstance(record, dict) else None
    return [s for s in scopes if isinstance(s, str) and s][:MAX_SCOPES] if isinstance(scopes, list) else []


async def clear_step_up_scopes(config_service: ConfigurationService, instance_id: str, owner_id: str) -> None:
    """After a sign-in: what it asked for is granted or refused, and a 403 says so again."""
    try:
        await config_service.delete_config(get_mcp_step_up_scopes_path(instance_id, owner_id))
    except Exception:
        logger.warning("Couldn't clear the scopes saved for MCP instance %s", instance_id, exc_info=True)


def needs_more_scopes_message(server_name: str, scopes: list[str], *, reconnect_in: str) -> str:
    """What the model is told."""
    return (
        f"The {server_name} MCP server needs more permission for this (scopes: {', '.join(scopes)}). "
        f"Ask the user to reconnect it in {reconnect_in} to grant it."
    )
