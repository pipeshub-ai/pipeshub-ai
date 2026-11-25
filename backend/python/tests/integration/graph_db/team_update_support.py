"""The team edit `PUT /api/v1/entity/team/{id}` runs against a real graph store: seeding, the request, and the team as stored.

Shared by the Neo4j/ArangoDB file and the PostgreSQL one, so every store answers the same cases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.routes.entity import update_team
from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgContext, bind
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

USERS, TEAMS, PERMISSION = CollectionNames.USERS.value, CollectionNames.TEAMS.value, CollectionNames.PERMISSION.value


@dataclass
class TeamWorld:
    graph: IGraphDBProvider
    org_id: str
    team_key: str
    owner: tuple[str, str]      # (Mongo userId, graph key) of the caller, the team's owner
    stays: tuple[str, str]      # a reader the edit makes a writer
    leaves: tuple[str, str]     # a reader the edit removes

    def keys(self) -> dict[str, list[str]]:
        return {USERS: [self.owner[1], self.stays[1], self.leaves[1]], TEAMS: [self.team_key]}

    def edit(self) -> dict:
        return {"name": "after", "removeUserIds": [self.leaves[0]],
                "updateUserRoles": [{"userId": self.stays[0], "role": "WRITER"}]}


def plan_team(graph: IGraphDBProvider, org_id: str, run: str) -> TeamWorld:
    """The ids of a team and its people, before anything is written: a fixture registers their clean-up from it first."""
    owner, stays, leaves = ((f"uid-{name}-{run}", f"key-{name}-{run}") for name in ("owner", "stays", "leaves"))
    return TeamWorld(graph, org_id, f"team-{run}", owner, stays, leaves)


async def seed_team(world: TeamWorld) -> None:
    """A team named "before": its owner, and two readers."""
    graph, people = world.graph, {"owner": world.owner, "stays": world.stays, "leaves": world.leaves}
    assert await graph.batch_upsert_nodes(
        [{"_key": key, "userId": uid, "orgId": world.org_id, "email": f"{uid}@example.com", "isActive": True}
         for uid, key in people.values()],
        USERS,
    )
    now = get_epoch_timestamp_in_ms()
    assert await graph.batch_upsert_nodes(
        [{"_key": world.team_key, "name": "before", "orgId": world.org_id, "createdBy": world.owner[1],
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        TEAMS,
    )
    roles = {"owner": "OWNER", "stays": "READER", "leaves": "READER"}
    assert await graph.batch_create_edges(
        [{"from_id": people[name][1], "from_collection": USERS, "to_id": world.team_key, "to_collection": TEAMS,
          "type": "USER", "role": role, "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for name, role in roles.items()],
        PERMISSION,
    )


async def stored(world: TeamWorld) -> tuple[str, dict[str, str | None]]:
    """The team's name and each seeded person's role on it (None: not a member)."""
    with bind(OrgContext(world.org_id, world.owner[0], "user", "jwt", "it-team-update")):
        team = await world.graph.get_document(world.team_key, TEAMS)
        roles = {}
        for uid, key in (world.owner, world.stays, world.leaves):
            edge = await world.graph.get_edge(key, USERS, world.team_key, TEAMS, PERMISSION, raise_on_error=True)
            roles[uid] = edge.get("role") if edge else None
    return team["name"], roles


async def send_edit(world: TeamWorld, body: dict) -> tuple[int, str | None]:
    """The status and error detail the team service answers the owner's edit with."""
    request = MagicMock()
    request.state.user = {"userId": world.owner[0], "orgId": world.org_id}
    request.app.container.logger.return_value = MagicMock()
    request.app.state.graph_provider = world.graph
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    with bind(OrgContext(world.org_id, world.owner[0], "user", "jwt", "it-team-update")), \
            patch("app.api.routes.entity._caller_is_an_administrator", AsyncMock(return_value=False)), \
            patch("app.api.routes.entity._principals_changed", AsyncMock()):
        try:
            return (await update_team(request, world.team_key)).status_code, None
        except HTTPException as exc:
            return exc.status_code, exc.detail

