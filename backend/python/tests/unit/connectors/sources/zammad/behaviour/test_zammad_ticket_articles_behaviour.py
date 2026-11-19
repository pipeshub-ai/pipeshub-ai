"""Ticket articles as Zammad sends them, with null fields, and a stream that can't read them.

An article made through the API as an e-mail has no ``from``, and ``preferences``,
``attachments`` or ``body`` may be null. A ticket whose articles can't be read
must fail its stream: streamed as its title alone, it would be indexed that way
and stay so until the ticket next changes in Zammad.

The connector's sync and ``stream_record`` run as they do in production; Zammad
and our stores are the in-memory fakes in ``zammad_behaviour_fakes``.
"""

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeRecordsDb,
    FakeStore,
    FakeZammad,
)

from app.connectors.sources.zammad.connector import (
    ZammadConnector,
    ZammadSyncIncompleteError,
)

SUPPORT = 1
PDF = {"id": 5, "filename": "report.pdf", "size": 3, "preferences": {"Content-Type": "application/pdf"}}


def _article(article_id: int, **fields: object) -> dict:
    return {"id": article_id, "sender": "Customer", "from": "c@example.com", "subject": None,
            "preferences": {}, "attachments": [], "body": f"<p>article {article_id}</p>",
            "created_at": f"2026-01-0{article_id}T00:00:00Z", **fields}


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammad(groups={SUPPORT: "Support"})
        self.db = FakeRecordsDb()
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.articles"),
                data_entities_processor=self.db,
                data_store_provider=FakeStore(),
                config_service=FakeConfigService(),
                connector_id="zm-articles",
                scope="team",
                created_by="admin",
            )
        self.connector.external_client = object()
        self.connector.base_url = "https://zammad.test"
        self.connector._get_fresh_datasource = AsyncMock(return_value=self.zammad)
        self.connector._fetch_users = AsyncMock(return_value=([], {}))
        self.connector._sync_roles = AsyncMock()
        self.connector._sync_knowledge_bases = AsyncMock()

    async def sync(self) -> None:
        await self.connector.run_sync()

    async def stream(self, external_id: str) -> dict:
        response = await self.connector.stream_record(self.db.records[external_id])
        return json.loads(b"".join([chunk async for chunk in response.body_iterator]))


def _comments(container: dict) -> list[str]:
    return [g["data"] for g in container["block_groups"] if g.get("sub_type") == "comment"]


@pytest.fixture
def world() -> World:
    return World()


@pytest.mark.parametrize("null_field", ["from", "preferences", "attachments", "body", "subject", "created_at"])
async def test_an_article_with_a_null_field_is_synced_and_streamed(world: World, null_field: str) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = [
        _article(1),
        _article(2, **{"attachments": [PDF], null_field: None}),
        _article(3),
    ]

    await world.sync()
    container = await world.stream("12")

    assert "12" in world.db.external_ids()
    if null_field != "attachments":
        assert "12_2_5" in world.db.external_ids()
    streamed = [g["data"] for g in container["block_groups"]]
    # With no created_at the article sorts first and becomes the description.
    if null_field != "body":
        assert any("article 2" in data for data in streamed)
    assert any("article 3" in c for c in _comments(container))


async def test_a_first_article_with_null_fields_still_makes_the_description(world: World) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = [_article(1, body=None, attachments=None, preferences=None, **{"from": None})]

    await world.sync()
    container = await world.stream("12")

    assert container["block_groups"][0]["data"] == "# ticket 12"


@pytest.mark.parametrize(("field", "value"), [
    ("sender", "System"),
    ("preferences", {"is-auto-response": True}),
    ("preferences", {"send-auto-response": False}),
    ("from", "MAILER-DAEMON@mail.example.com"),
    ("from", "Mail Delivery System <postmaster@example.com>"),
])
async def test_system_notes_auto_replies_and_bounces_are_left_out(world: World, field: str, value: object) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = [_article(1), _article(2, attachments=[PDF], **{field: value})]

    await world.sync()
    container = await world.stream("12")

    assert "12_2_5" not in world.db.external_ids()
    assert _comments(container) == []


async def test_an_entry_that_is_not_an_article_fails_the_ticket_not_the_sync(world: World) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.add_ticket(13, SUPPORT, day=2)
    world.zammad.articles[12] = [_article(1), "not an article"]

    with pytest.raises(ZammadSyncIncompleteError):
        await world.sync()

    assert "13" in world.db.external_ids()
    assert "12" not in world.db.external_ids()


async def test_a_ticket_whose_articles_fail_to_read_fails_its_stream(world: World) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = [_article(1)]
    await world.sync()

    world.zammad.fail_articles_for.add(12)
    with pytest.raises(HTTPException) as raised:
        await world.stream("12")

    assert raised.value.status_code >= 500


@pytest.mark.parametrize("body", [{"error": "Not authorized"}, {"id": 1, "body": "one article, not a list"}])
async def test_an_article_answer_that_is_not_a_list_fails_the_stream(world: World, body: object) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = [_article(1)]
    await world.sync()

    world.zammad.articles[12] = body
    with pytest.raises(HTTPException):
        await world.stream("12")


async def test_a_ticket_with_no_articles_streams_its_title(world: World) -> None:
    world.zammad.add_ticket(12, SUPPORT, day=1)
    world.zammad.articles[12] = []
    await world.sync()

    container = await world.stream("12")

    assert [g["data"] for g in container["block_groups"]] == ["# ticket 12"]
