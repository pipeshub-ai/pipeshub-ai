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

from app.connectors.services.connector_intents import ConnectorIntentStore

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

PENDING_DIRECTORY = "/services/entityCleanup/pending/"


class EntityCleanupIntentError(Exception):
    """The intent could not be recorded; the deletion must not go ahead."""


_STORE = ConnectorIntentStore(PENDING_DIRECTORY, EntityCleanupIntentError, "entity cleanup")


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
    await _STORE.record(
        config_service, org_id=org_id, connector_id=connector_id, now_ms=now_ms,
        connectorName=connector_name,
    )


async def clear_pending_entity_cleanup(config_service: ConfigurationService, connector_id: str) -> bool:
    """Forget the intent once the cleanup finished."""
    return await _STORE.clear(config_service, connector_id)


async def reschedule_pending_entity_cleanup(
    config_service: ConfigurationService, intent: dict[str, Any], *, next_attempt_at: int,
) -> bool:
    """Count a failed attempt on ``intent`` and hold it until ``next_attempt_at``."""
    return await _STORE.reschedule(config_service, intent, next_attempt_at=next_attempt_at)


async def list_pending_entity_cleanups(config_service: ConfigurationService) -> list[dict[str, Any]]:
    """Every recorded intent, oldest first; malformed entries are skipped."""
    return await _STORE.list(config_service)
