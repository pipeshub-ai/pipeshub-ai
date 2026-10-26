"""Giving a connector its lane when it is created, and freeing it when it goes.

Both are best-effort. A connector that could not be given a lane at creation
gets one on its first publish, and a lane that was not freed at delete is
freed by the indexing service's upkeep once it sees the connector gone, so a
failure here is logged and never fails the creation or the delete.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import Connectors, ConnectorScopes
from app.services.messaging.config import Topic
from app.services.messaging.lanes.assignment import lane_assignments_in_use
from app.services.messaging.lanes.assignment_policy import ConnectorClass

if TYPE_CHECKING:
    from logging import Logger

__all__ = [
    "assign_lane_to_new_connector",
    "connector_class_of",
    "free_lane_of_deleted_connector",
]


def connector_class_of(connector_type: str | None, scope: str | None) -> str:
    """The lane class of a connector, from its apps document."""
    if connector_type == Connectors.KNOWLEDGE_BASE.value:
        return ConnectorClass.KB.value
    if scope == ConnectorScopes.PERSONAL.value:
        return ConnectorClass.PERSONAL.value
    return ConnectorClass.TEAM.value


async def assign_lane_to_new_connector(
    logger: Logger,
    connector_id: str,
    *,
    connector_type: str | None,
    scope: str | None,
    org_id: str | None,
) -> None:
    """Place a just-created connector or knowledge base, while its class is known for sure."""
    assignments = lane_assignments_in_use(Topic.RECORD_EVENTS.value)
    if assignments is None:
        return
    try:
        await assignments.assign(
            connector_id,
            connector_class_of(connector_type, scope),
            org_id=org_id,
            connector_type=connector_type,
            is_new=True,
        )
    except Exception as e:
        logger.warning(
            "Could not give connector %s a queue lane at creation; it gets one on "
            "its first sync instead: %s: %s",
            connector_id,
            type(e).__name__,
            e,
        )


async def free_lane_of_deleted_connector(logger: Logger, connector_id: str) -> None:
    """Free a deleted connector's lane for the next one, after its delete events are sent."""
    assignments = lane_assignments_in_use(Topic.RECORD_EVENTS.value)
    if assignments is None:
        return
    try:
        await assignments.release(connector_id)
    except Exception as e:
        logger.warning(
            "Could not free the queue lane of deleted connector %s; the indexing "
            "service frees it within a few minutes: %s: %s",
            connector_id,
            type(e).__name__,
            e,
        )
