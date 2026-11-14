"""Notion personal access tokens that PipesHub users link to a Notion team connector.

A personal access token acts as the Notion member who created it, with that member's own page
permissions (developers.notion.com/guides/connections-and-tokens/personal-access-tokens). Searching
with it lists what that member may open, which is the only per-person access Notion's API shows.

One entry per PipesHub user under the connector's config, in the encrypted configuration store like
every other connector credential. The token is never returned by the API, logged or put in an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

LINKED = "LINKED"
# The token stopped working (revoked, expired, its creator left the workspace) or is not that
# user's own; the secret is dropped and the user must link a new one.
FAILED = "FAILED"


def tokens_dir(connector_id: str) -> str:
    return f"/services/connectors/{connector_id}/personalTokens/"


def token_path(connector_id: str, user_id: str) -> str:
    return f"{tokens_dir(connector_id)}{user_id}"


async def link_token(config_service: ConfigurationService, connector_id: str, user_id: str, token: str) -> None:
    """Store ``user_id``'s token for the connector, replacing any earlier one."""
    now = get_epoch_timestamp_in_ms()
    if not await config_service.set_config(
        token_path(connector_id, user_id), {"token": token, "status": LINKED, "linkedAt": now, "updatedAt": now},
    ):
        raise RuntimeError("the configuration store did not save the token")


async def unlink_token(config_service: ConfigurationService, connector_id: str, user_id: str) -> None:
    await config_service.delete_config(token_path(connector_id, user_id))


async def mark_failed(config_service: ConfigurationService, connector_id: str, user_id: str, reason: str) -> None:
    """Drop the secret and keep why, so the user and admins see it until a new token is linked."""
    await config_service.set_config(token_path(connector_id, user_id), {
        "status": FAILED, "reason": reason, "updatedAt": get_epoch_timestamp_in_ms(),
    })


async def entries(config_service: ConfigurationService, connector_id: str) -> dict[str, dict[str, Any]]:
    """user id -> stored entry, for every user who linked a token to the connector."""
    prefix = tokens_dir(connector_id)
    out: dict[str, dict[str, Any]] = {}
    for key in await config_service.list_keys_in_directory(prefix):
        user_id = key[len(prefix):] if key.startswith(prefix) else ""
        if not user_id or "/" in user_id:
            continue
        value = await config_service.get_config(key, use_cache=False)
        if isinstance(value, dict):
            out[user_id] = value
    return out


def public_view(entry: dict[str, Any] | None) -> dict[str, Any]:
    """What a user or an admin may see of an entry: never the token."""
    if not entry:
        return {"status": "NOT_LINKED"}
    return {k: entry.get(k) for k in ("status", "reason", "linkedAt", "updatedAt", "checkedAt") if entry.get(k) is not None}
