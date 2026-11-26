"""The routes' side of the control plane's directory (CPD-9): publishing ``entity-events`` directory events and
deciding an agent's owner team, over this service's broker and graph.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.arangodb import CollectionNames
from app.control_plane.directory.events import DirectoryEventPublisher, MessageProducer
from app.control_plane.directory.owner_team import owner_team_of
from app.core.org_context import org_of
from app.services.messaging.config import get_message_broker_type
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import Request

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_PRODUCER_STATE = "control_plane_directory_producer"
_PRODUCER_LOCK = asyncio.Lock()


async def _producer(request: Request) -> MessageProducer | None:
    """The service's broker producer: the connectors service's own, else one made once for this process."""
    container = request.app.container
    existing = getattr(container, "messaging_producer", None)
    if existing is not None:
        return existing
    state = request.app.state
    cached = getattr(state, _PRODUCER_STATE, None)
    if cached is not None:
        return cached
    async with _PRODUCER_LOCK:
        cached = getattr(state, _PRODUCER_STATE, None)
        if cached is None:
            config = await MessagingUtils.create_producer_config_from_service(container.config_service(), "directory_events")
            cached = MessagingFactory.create_producer(broker_type=get_message_broker_type(), logger=container.logger(), config=config)
            await cached.initialize()
            setattr(state, _PRODUCER_STATE, cached)
    return cached


def directory_events(request: Request) -> DirectoryEventPublisher:
    return DirectoryEventPublisher(lambda: _producer(request))


async def owner_team_from(
    request: Request,
    raw: object,
    *,
    graph: IGraphDBProvider,
    org_id: str,
    user_key: str,
) -> str | None:
    """The owner team an agent create or update asks for, checked against the org's teams and the caller's role."""

    async def team_in_org(team_id: str) -> bool:
        team = cast("Mapping[str, object] | None", await graph.get_document(team_id, CollectionNames.TEAMS.value))  # pyright: ignore[reportUnknownMemberType] - the provider types documents loosely
        return team is not None and org_of(team) == org_id and not team.get("isDeleted", False)

    async def is_org_admin() -> bool:
        return (await fetch_caller_role(request, request.app.container.config_service())).is_admin

    async def team_role(team_id: str) -> str | None:
        edge = cast(
            "Mapping[str, object] | None",
            await graph.get_edge(  # pyright: ignore[reportUnknownMemberType] - as above
                user_key, CollectionNames.USERS.value, team_id, CollectionNames.TEAMS.value, CollectionNames.PERMISSION.value
            ),
        )
        role = None if not edge else edge.get("role")
        return role if isinstance(role, str) else None

    return await owner_team_of(raw, team_in_org=team_in_org, is_org_admin=is_org_admin, team_role=team_role)
