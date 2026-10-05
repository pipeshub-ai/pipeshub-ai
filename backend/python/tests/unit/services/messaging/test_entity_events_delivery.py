"""Entity events (organisations, users, apps) from the broker to the graph.

The Node.js API publishes an event when an organisation is created, a user
is added or an app is switched off; the connectors service turns each into
graph documents. These tests send the events through the real Kafka
consumer and the real entity handler into an in-memory graph, then check
what a customer relies on: a redelivered event changes nothing, and an
event about one organisation writes only that organisation's documents.
Only the broker and the graph database are stubbed.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.messaging.config import StreamMessage
from app.services.messaging.kafka.config.kafka_config import KafkaConsumerConfig
from app.services.messaging.kafka.consumer import consumer as consumer_module
from app.services.messaging.kafka.consumer.consumer import KafkaMessagingConsumer
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.utils.utils import KafkaUtils
from tests.support.fake_kafka import FakeKafkaBroker

if TYPE_CHECKING:
    from collections.abc import Iterator

TOPIC = "entity-events"
GROUP = "entity_consumer_group"


class InMemoryGraph:
    """The slice of the graph provider the entity handler uses."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, dict]] = {}
        self.edges: dict[str, list[dict]] = {}
        self.fail_next: dict[str, BaseException] = {}

    def _maybe_fail(self, operation: str) -> None:
        error = self.fail_next.pop(operation, None)
        if error is not None:
            raise error

    async def get_document(self, key: str, collection: str, **_: Any) -> dict | None:  # noqa: ANN401
        return self.nodes.get(collection, {}).get(key)

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._maybe_fail(f"upsert:{collection}")
        bucket = self.nodes.setdefault(collection, {})
        for node in nodes:
            key = node.get("id") or node.get("_key")
            bucket[key] = {**bucket.get(key, {}), **node}
        return True

    async def batch_create_edges(self, edges: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._maybe_fail(f"edges:{collection}")
        bucket = self.edges.setdefault(collection, [])
        for edge in edges:
            # Same rule as the Arango and Neo4j providers: UPSERT on (from, to).
            match = next(
                (e for e in bucket if e["from_id"] == edge["from_id"] and e["to_id"] == edge["to_id"]),
                None,
            )
            if match is None:
                bucket.append(dict(edge))
            else:
                match.update(edge)
        return True

    async def get_nodes_by_filters(self, collection: str, filters: dict) -> list[dict]:
        return [
            dict(n) for n in self.nodes.get(collection, {}).values()
            if all(n.get(k) == v for k, v in filters.items())
        ]

    async def get_user_by_email(self, email: str, transaction: str | None = None) -> SimpleNamespace | None:
        for node in self.nodes.get(CollectionNames.USERS.value, {}).values():
            if node.get("email") == email:
                return SimpleNamespace(id=node["id"])
        return None

    async def get_user_by_user_id(self, user_id: str, **_: Any) -> dict | None:  # noqa: ANN401
        for node in self.nodes.get(CollectionNames.USERS.value, {}).values():
            if node.get("userId") == user_id:
                return dict(node)
        return None

    async def get_entity_id_by_email(self, email: str, transaction: str | None = None) -> str | None:
        user = await self.get_user_by_email(email)
        return user.id if user else None

    async def add_user_to_all_team(self, org_id: str, user_key: str) -> None:
        await self.batch_create_edges(
            [{"from_id": user_key, "to_id": f"all_{org_id}", "type": "USER"}],
            CollectionNames.PERMISSION.value,
        )

    async def begin_transaction(self, read: list[str], write: list[str]) -> str:
        return "txn-1"

    async def commit_transaction(self, txn_id: str) -> None:
        return None

    async def rollback_transaction(self, txn_id: str) -> None:
        return None

    async def reset_indexing_status_for_connector(self, connector_id: str, status: str, **_: Any) -> None:  # noqa: ANN401
        return None

    def kb_apps(self) -> list[dict]:
        return [a for a in self.nodes.get(CollectionNames.APPS.value, {}).values()
                if a.get("type") == Connectors.KNOWLEDGE_BASE.value]


@pytest.fixture
def broker() -> Iterator[FakeKafkaBroker]:
    broker = FakeKafkaBroker()
    with patch.object(consumer_module, "AIOKafkaConsumer", broker.consumer_factory()), \
            patch.object(entity_module.ConnectorFactory, "create_and_start_sync", AsyncMock(return_value=None)):
        yield broker


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGE_TIMEOUT_MS", "10")


@pytest.fixture
def graph() -> InMemoryGraph:
    return InMemoryGraph()


def _container() -> MagicMock:
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    container.data_store = AsyncMock(return_value=MagicMock())
    container.messaging_producer.send_message = AsyncMock(return_value=True)
    del container.connectors_map
    return container


async def _deliver(broker: FakeKafkaBroker, graph: InMemoryGraph, *events: dict) -> KafkaMessagingConsumer:
    start = broker.committed_offset(GROUP, TOPIC) or 0
    for event in events:
        broker.produce(TOPIC, event)
    handler = await KafkaUtils.create_entity_message_handler(_container(), graph)
    consumer = KafkaMessagingConsumer(
        logging.getLogger("test"),
        KafkaConsumerConfig(
            topics=[TOPIC], client_id="c", group_id=GROUP, auto_offset_reset="earliest",
            enable_auto_commit=False, bootstrap_servers=["kafka:9092"],
        ),
    )
    await consumer.start(handler)
    deadline = asyncio.get_running_loop().time() + 5
    while (broker.committed_offset(GROUP, TOPIC) or 0) < start + len(events):
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("events were not all committed")
        await asyncio.sleep(0.01)
    await consumer.stop()
    return consumer


def _org_created(org_id: str) -> dict:
    return {"eventType": "orgCreated", "payload": {
        "orgId": org_id, "accountType": "enterprise", "registeredName": f"{org_id} Inc", "userId": "u-admin",
    }}


def _user_added(org_id: str, user_id: str, email: str) -> dict:
    return {"eventType": "userAdded", "payload": {
        "orgId": org_id, "userId": user_id, "email": email, "fullName": "Ada Lovelace",
    }}


class TestRedeliveredEventsChangeNothing:
    async def test_org_created_twice_leaves_one_org_and_one_all_team(self, broker, graph) -> None:
        await _deliver(broker, graph, _org_created("org-a"), _org_created("org-a"))
        assert list(graph.nodes[CollectionNames.ORGS.value]) == ["org-a"]
        assert list(graph.nodes[CollectionNames.TEAMS.value]) == ["all_org-a"]

    async def test_user_added_twice_leaves_one_user_one_private_kb_and_unique_edges(self, broker, graph) -> None:
        await _deliver(broker, graph, _org_created("org-a"))
        event = _user_added("org-a", "u-1", "ada@a.example")
        await _deliver(broker, graph, event)
        await _deliver(broker, graph, event)

        users = graph.nodes[CollectionNames.USERS.value]
        assert len(users) == 1
        assert len(graph.kb_apps()) == 1
        for collection in (
            CollectionNames.BELONGS_TO.value,
            CollectionNames.PERMISSION.value,
            CollectionNames.ORG_APP_RELATION.value,
            CollectionNames.USER_APP_RELATION.value,
        ):
            pairs = [(e["from_id"], e["to_id"]) for e in graph.edges[collection]]
            assert len(pairs) == len(set(pairs)), collection


class TestOrganisationScoping:
    async def test_a_user_added_to_org_b_writes_only_org_b_documents(self, broker, graph) -> None:
        await _deliver(broker, graph, _org_created("org-a"), _org_created("org-b"))
        await _deliver(broker, graph, _user_added("org-a", "u-a", "ada@a.example"))
        snapshot_a_user = dict(next(iter(graph.nodes[CollectionNames.USERS.value].values())))
        snapshot_a_kb = dict(graph.kb_apps()[0])

        await _deliver(broker, graph, _user_added("org-b", "u-b", "bob@b.example"))

        users = {u["userId"]: u for u in graph.nodes[CollectionNames.USERS.value].values()}
        assert users["u-a"] == snapshot_a_user
        assert users["u-b"]["orgId"] == "org-b"
        kbs = {kb["createdBy"]: kb for kb in graph.kb_apps()}
        assert kbs["u-a"] == snapshot_a_kb
        assert kbs["u-b"]["orgId"] == "org-b"
        org_edges = [e for e in graph.edges[CollectionNames.BELONGS_TO.value] if e["from_id"] == users["u-b"]["id"]]
        assert [e["to_id"] for e in org_edges] == ["org-b"]
        team_edges = [e for e in graph.edges[CollectionNames.PERMISSION.value] if e["to_id"].startswith("all_")]
        assert {(e["from_id"], e["to_id"]) for e in team_edges} == {
            (users["u-a"]["id"], "all_org-a"),
            (users["u-b"]["id"], "all_org-b"),
        }

    async def test_the_private_kb_lookup_is_per_organisation(self, broker, graph) -> None:
        # The same Node user id in a second org gets its own KB, never org A's.
        await _deliver(broker, graph, _org_created("org-a"), _org_created("org-b"))
        await _deliver(broker, graph, _user_added("org-a", "u-same", "one@a.example"))
        await _deliver(broker, graph, _user_added("org-b", "u-same", "two@b.example"))
        assert sorted(kb["orgId"] for kb in graph.kb_apps()) == ["org-a", "org-b"]

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Left alone: entity.py is part of open PRs #3128, #3115 and #2675. The "
            "appDisabled handler looks the app up by connectorId only and never checks "
            "it belongs to the event's orgId, so a mis-addressed event would switch off "
            "another organisation's connector. The open-source edition allows one "
            "organisation per deployment, so this needs a multi-org deployment to matter."
        ),
    )
    async def test_app_disabled_for_org_a_cannot_switch_off_an_org_b_connector(self, broker, graph) -> None:
        await _deliver(broker, graph, _org_created("org-a"), _org_created("org-b"))
        graph.nodes[CollectionNames.APPS.value] = {
            "conn-b": {"id": "conn-b", "orgId": "org-b", "name": "Drive", "type": "DRIVE", "isActive": True},
        }
        await _deliver(broker, graph, {"eventType": "appDisabled", "payload": {
            "orgId": "org-a", "apps": ["DRIVE"], "connectorId": "conn-b",
        }})
        assert graph.nodes[CollectionNames.APPS.value]["conn-b"]["isActive"] is True


class TestPartialFailure:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Left alone: entity.py is part of open PRs. When creating the user's "
            "private knowledge base fails, the error is logged and swallowed, so the "
            "event is acknowledged and nothing ever retries it: the user exists but has "
            "no private knowledge base."
        ),
    )
    async def test_a_brief_graph_failure_while_creating_the_private_kb_is_reported_for_retry(
        self, broker, graph
    ) -> None:
        await _deliver(broker, graph, _org_created("org-a"))
        graph.fail_next[f"upsert:{CollectionNames.APPS.value}"] = ConnectionError("graph database unreachable")
        handler = await KafkaUtils.create_entity_message_handler(_container(), graph)
        message = StreamMessage(**_user_added("org-a", "u-1", "ada@a.example"))
        handled = await handler(message)
        assert graph.kb_apps() == []
        assert handled is False
