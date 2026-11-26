"""``teamMembershipChanged`` and ``agentChanged`` on ``entity-events`` (02-lld §8.5).

Node caches team memberships and agent access per org; these events drop its entries at once, its 30 s TTL is the
backstop when one is lost. They carry ids only. Publishing never fails the route that made the change: the change
is already committed, so a broker outage is logged once and the TTL covers it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.utils.time_conversion import get_epoch_timestamp_in_ms
from harness_core.logs import HarnessLog

ENTITY_EVENTS_TOPIC = "entity-events"
TEAM_MEMBERSHIP_CHANGED = "teamMembershipChanged"
AGENT_CHANGED = "agentChanged"
# For the platform's own entity-events consumer, which acknowledges these without work.
DIRECTORY_EVENT_TYPES = frozenset({TEAM_MEMBERSHIP_CHANGED, AGENT_CHANGED})

_LOG = HarnessLog.named("app.control_plane.directory")
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$"


class TeamMembershipChanged(BaseModel):
    """Members of a team added, removed or re-roled, or the team deleted (``userIds`` then empty: every member)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    org_id: str = Field(alias="orgId", pattern=_ID)
    team_id: str = Field(alias="teamId", pattern=_ID)
    user_ids: tuple[str, ...] = Field(alias="userIds", max_length=10_000)


class AgentChanged(BaseModel):
    """An agent's definition, sharing or owner team changed, or the agent was deleted (``definitionVersion`` null)."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    org_id: str = Field(alias="orgId", pattern=_ID)
    agent_key: str = Field(alias="agentKey", pattern=_ID)
    definition_version: int | None = Field(alias="definitionVersion", default=None)


class MessageProducer(Protocol):
    async def send_message(self, topic: str, message: dict[str, Any], key: str | None = None) -> bool: ...


ProducerSource = Callable[[], Awaitable[MessageProducer | None]]


class DirectoryEventPublisher:
    """Publishes the directory events keyed by org, so one org's events keep their order."""

    def __init__(self, producer: ProducerSource) -> None:
        self._producer = producer

    async def team_membership_changed(self, org_id: str, team_id: str, user_ids: Sequence[str]) -> None:
        await self._publish(
            TEAM_MEMBERSHIP_CHANGED,
            org_id,
            lambda: TeamMembershipChanged(orgId=org_id, teamId=team_id, userIds=tuple(sorted(set(user_ids)))),
        )

    async def agent_changed(self, org_id: str, agent_key: str, definition_version: int | None) -> None:
        await self._publish(
            AGENT_CHANGED,
            org_id,
            lambda: AgentChanged(orgId=org_id, agentKey=agent_key, definitionVersion=definition_version),
        )

    async def _publish(self, event_type: str, org_id: str, event: Callable[[], BaseModel]) -> None:
        try:
            payload = event().model_dump(by_alias=True, mode="json")
            message = {"eventType": event_type, "payload": payload, "timestamp": get_epoch_timestamp_in_ms()}
            producer = await self._producer()
            sent = producer is not None and await producer.send_message(ENTITY_EVENTS_TOPIC, message, key=org_id)
        except Exception as exc:
            _LOG.warning("directory.event_not_published", orgId=org_id, eventType=event_type, errorType=type(exc).__name__)
            return
        if not sent:
            _LOG.warning("directory.event_not_published", orgId=org_id, eventType=event_type, errorType="not_sent")
