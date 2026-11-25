"""CPD-9 DE-2, DE-3: the directory events on ``entity-events`` (02-lld §8.5).

Team create, update and delete publish ``teamMembershipChanged``; the publisher keys by org, carries ids only and
never fails the route; the platform's own entity consumer acknowledges both types. Only the broker and the graph
are fakes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.entity import create_team, delete_team, update_team
from app.control_plane.directory.events import (
    AGENT_CHANGED,
    ENTITY_EVENTS_TOPIC,
    TEAM_MEMBERSHIP_CHANGED,
    DirectoryEventPublisher,
)
from app.core.org_context import OrgContext, bind
from app.services.messaging.kafka.handlers.entity import EntityEventService

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _caller_org_is_bound() -> Iterator[None]:
    """The auth middleware binds the caller's org (main #4703); these tests call the team handlers directly."""
    with bind(OrgContext("org-1", "65f1c2e4a9b8c7d6e5f4a3b2", "user", "jwt", "req-test")):
        yield


class RecordingProducer:
    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[tuple[str, dict[str, Any], str | None]] = []
        self.fails = fails

    async def send_message(self, topic: str, message: dict[str, Any], key: str | None = None) -> bool:
        if self.fails:
            raise ConnectionError("broker down")
        self.sent.append((topic, message, key))
        return True


def _publisher(producer: RecordingProducer | None) -> DirectoryEventPublisher:
    async def source() -> RecordingProducer | None:
        return producer

    return DirectoryEventPublisher(source)


async def test_de2_team_membership_changed_is_keyed_by_org_with_ids_only() -> None:
    producer = RecordingProducer()
    await _publisher(producer).team_membership_changed("org-1", "team-9", ["u2", "u1", "u2"])
    [(topic, message, key)] = producer.sent
    assert (topic, key) == (ENTITY_EVENTS_TOPIC, "org-1")
    assert message["eventType"] == TEAM_MEMBERSHIP_CHANGED
    assert message["payload"] == {"orgId": "org-1", "teamId": "team-9", "userIds": ["u1", "u2"]}


async def test_de2_agent_changed_names_the_agent_and_its_version() -> None:
    producer = RecordingProducer()
    await _publisher(producer).agent_changed("org-1", "agent-7", 42)
    await _publisher(producer).agent_changed("org-1", "agent-7", None)
    assert [m["payload"] for _t, m, _k in producer.sent] == [
        {"orgId": "org-1", "agentKey": "agent-7", "definitionVersion": 42},
        {"orgId": "org-1", "agentKey": "agent-7", "definitionVersion": None},
    ]
    assert {m["eventType"] for _t, m, _k in producer.sent} == {AGENT_CHANGED}


@pytest.mark.parametrize("producer", [RecordingProducer(fails=True), None], ids=["broker-down", "no-producer"])
async def test_de2_a_publish_that_fails_is_logged_and_never_raised(producer: RecordingProducer | None) -> None:
    with patch("app.control_plane.directory.events._LOG") as log:
        await _publisher(producer).team_membership_changed("org-1", "team-9", ["u1"])
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs["eventType"] == TEAM_MEMBERSHIP_CHANGED


async def test_de2_an_id_the_event_cannot_carry_is_logged_not_raised() -> None:
    producer = RecordingProducer()
    with patch("app.control_plane.directory.events._LOG") as log:
        await _publisher(producer).team_membership_changed("", "team-9", ["u1"])
    assert producer.sent == []
    log.warning.assert_called_once()


def _request(body: dict[str, Any], producer: RecordingProducer) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "65f1c2e4a9b8c7d6e5f4a3b2", "orgId": "org-1"}
    request.app.container = MagicMock()
    request.app.container.messaging_producer = producer
    request.app.state.graph_provider = AsyncMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode("utf-8"))
    graph = request.app.state.graph_provider
    graph.get_user_by_user_id.return_value = {"_key": "creator-key"}
    graph.find_team_by_name.return_value = None  # main's duplicate-name check: the name is free
    graph.begin_transaction.return_value = "tx-1"
    graph.batch_upsert_nodes.return_value = True
    graph.batch_create_edges.return_value = True
    graph.get_team_with_users.return_value = {"team": "data"}
    graph.get_graph_user_keys_by_mongo_user_ids.side_effect = lambda ids, **_: {i: f"key-{i}" for i in ids}
    return request


def _membership_events(producer: RecordingProducer) -> list[dict[str, Any]]:
    return [m["payload"] for _t, m, _k in producer.sent if m["eventType"] == TEAM_MEMBERSHIP_CHANGED]


async def test_de2_creating_a_team_names_its_creator_and_members() -> None:
    producer = RecordingProducer()
    request = _request({"name": "T", "userRoles": [{"userId": "m1", "role": "WRITER"}]}, producer)
    with patch("app.api.routes.entity.uuid.uuid4", return_value="team-new"):
        response = await create_team(request)
    assert response.status_code == 200
    assert _membership_events(producer) == [
        {"orgId": "org-1", "teamId": "team-new", "userIds": ["65f1c2e4a9b8c7d6e5f4a3b2", "m1"]}
    ]


async def test_de2_updating_members_names_the_people_added_removed_and_re_roled() -> None:
    producer = RecordingProducer()
    body = {"addUserRoles": [{"userId": "m3", "role": "READER"}], "removeUserIds": ["m2"], "updateUserRoles": [{"userId": "m4", "role": "OWNER"}]}
    request = _request(body, producer)
    graph = request.app.state.graph_provider
    graph.get_edge.return_value = {"role": "OWNER"}
    graph.update_node.return_value = True
    with (
        patch("app.api.routes.entity._validate_owner_removal", new=AsyncMock()),
        patch("app.api.routes.entity._validate_and_filter_owner_updates", new=AsyncMock(return_value=([{"userId": "key-m4", "role": "OWNER"}], 2))),
    ):
        response = await update_team(request, "team-9")
    assert response.status_code == 200
    assert _membership_events(producer) == [{"orgId": "org-1", "teamId": "team-9", "userIds": ["m2", "m3", "m4"]}]


async def test_de2_renaming_a_team_publishes_nothing() -> None:
    producer = RecordingProducer()
    request = _request({"name": "Renamed"}, producer)
    request.app.state.graph_provider.get_edge.return_value = {"role": "OWNER"}
    request.app.state.graph_provider.update_node.return_value = True
    await update_team(request, "team-9")
    assert _membership_events(producer) == []


async def test_de2_deleting_a_team_drops_every_membership_of_it() -> None:
    producer = RecordingProducer()
    request = _request({}, producer)
    graph = request.app.state.graph_provider
    graph.get_document.return_value = {"_key": "team-9"}
    graph.get_edge.return_value = {"role": "OWNER"}
    graph.delete_nodes.return_value = True
    response = await delete_team(request, "team-9")
    assert response.status_code == 200
    assert _membership_events(producer) == [{"orgId": "org-1", "teamId": "team-9", "userIds": []}]


@pytest.mark.parametrize("event_type", [TEAM_MEMBERSHIP_CHANGED, AGENT_CHANGED])
async def test_de3_the_platform_entity_consumer_acknowledges_the_directory_events(event_type: str) -> None:
    app_container = MagicMock()
    service = EntityEventService(MagicMock(), AsyncMock(), app_container)
    assert await service.process_event(event_type, {"orgId": "org-1"}) is True
