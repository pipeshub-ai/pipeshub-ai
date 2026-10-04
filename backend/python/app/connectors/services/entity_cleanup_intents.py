"""Durable intent to clean a deleted connector's entity points.

Deleting a connector or KB removes its graph rows, then publishes
``deleteConnectorEntities``. A publish that fails after the graph delete
would otherwise leave the connector's record, record-group and taxonomy
membership in the entities collection for good, since nothing else knows
the connector existed. So the deleting service records an intent in the
KV store first; the indexing service clears it when the cleanup finishes,
and its rebuild loop runs any intent left behind (``EntityIndexRebuilder``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

PENDING_DIRECTORY = "/services/entityCleanup/pending/"


class EntityCleanupIntentError(Exception):
    """The intent could not be recorded; the deletion must not go ahead."""


def _key(connector_id: str) -> str:
    return f"{PENDING_DIRECTORY}{connector_id}"


async def record_pending_entity_cleanup(
    config_service: ConfigurationService,
    *,
    org_id: str,
    connector_id: str,
    connector_name: str | None,
    now_ms: int | None = None,
) -> None:
    """Record that ``connector_id``'s entity points need cleaning. Raises
    ``EntityCleanupIntentError`` when the KV store refuses the write."""
    intent = {
        "orgId": org_id,
        "connectorId": connector_id,
        "connectorName": connector_name,
        "requestedAt": get_epoch_timestamp_in_ms() if now_ms is None else now_ms,
    }
    if not await config_service.set_config(_key(connector_id), intent):
        raise EntityCleanupIntentError(f"could not record entity cleanup for connector {connector_id}")


async def clear_pending_entity_cleanup(config_service: ConfigurationService, connector_id: str) -> bool:
    """Forget the intent once the cleanup finished. A failed delete is
    returned, not raised: the intent then runs again, and cleanup is idempotent."""
    # Absent for deletes made before intents existed and for ones the rebuild
    # settled; Redis reports deleting a missing key as a failure.
    if await config_service.get_config(_key(connector_id), use_cache=False) is None:
        return True
    return bool(await config_service.delete_config(_key(connector_id)))


async def reschedule_pending_entity_cleanup(
    config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
) -> bool:
    """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``."""
    updated = {**intent, "attempts": int(intent.get("attempts") or 0) + 1, "nextAttemptAt": next_attempt_at}
    return bool(await config_service.set_config(_key(str(intent["connectorId"])), updated))


async def list_pending_entity_cleanups(config_service: ConfigurationService) -> list[dict[str, Any]]:
    """Every recorded intent, oldest first; malformed entries are skipped."""
    intents = []
    for key in await config_service.list_keys_in_directory(PENDING_DIRECTORY):
        value = await config_service.get_config(key, use_cache=False)
        if (
            isinstance(value, dict)
            and value.get("orgId")
            and value.get("connectorId")
            and key == _key(str(value["connectorId"]))
        ):
            intents.append(value)
    return sorted(intents, key=lambda i: (int(i.get("requestedAt") or 0), str(i["connectorId"])))
