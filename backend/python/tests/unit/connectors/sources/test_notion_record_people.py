"""Notion pages and data sources carry who created and last edited them."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.notion.connector import NotionConnector, notion_person
from app.models.entities import SourcePerson

ANN = "6794760a-1f15-45cd-9c65-0dfe42f5135a"
ED = "9a3b5ae0-c6e6-482d-b0e1-ed315ee6dc57"


@pytest.fixture()
def connector() -> NotionConnector:
    processor = MagicMock()
    processor.org_id = "org-notion"
    c = NotionConnector(
        logger=logging.getLogger("test.notion.people"),
        data_entities_processor=processor,
        data_store_provider=MagicMock(),
        config_service=AsyncMock(),
        connector_id="notion-1",
        scope="personal",
        created_by="test-user-id",
    )
    c.workspace_id = "ws-1"
    c._bot_ids_complete = True  # as after a user sync
    return c


# Page object as the search / pages API returns it: people are partial users.
PAGE = {
    "object": "page",
    "id": "59833787-2cf9-4fdf-8782-e53db20768a5",
    "created_time": "2026-03-01T09:00:00.000Z",
    "last_edited_time": "2026-03-02T10:00:00.000Z",
    "created_by": {"object": "user", "id": ANN},
    "last_edited_by": {"object": "user", "id": ED},
    "parent": {"type": "workspace", "workspace": True},
    "properties": {"title": {"type": "title", "title": [{"plain_text": "Roadmap"}]}},
    "url": "https://www.notion.so/Roadmap-598337872cf94fdf8782e53db20768a5",
}


async def test_page_maps_creator_and_last_editor(connector) -> None:
    record = await connector._transform_to_webpage_record(PAGE, "page")

    assert record.authored_by == SourcePerson(source_id=ANN)
    assert record.last_modified_by == SourcePerson(source_id=ED)
    assert record.created_by is None


async def test_data_source_maps_creator_and_last_editor(connector) -> None:
    data_source = {
        "object": "data_source",
        "id": "ds-1",
        "title": [{"plain_text": "Tasks"}],
        "created_time": "2026-03-01T09:00:00.000Z",
        "last_edited_time": "2026-03-02T10:00:00.000Z",
        "created_by": {"object": "user", "id": ANN},
        "last_edited_by": {"object": "user", "id": ED},
    }

    record = await connector._transform_to_webpage_record(data_source, "data_source")

    assert record.authored_by == SourcePerson(source_id=ANN)
    assert record.last_modified_by == SourcePerson(source_id=ED)


async def test_bot_editor_is_a_service_account(connector) -> None:
    page = {**PAGE, "last_edited_by": {"object": "user", "id": "bot-1", "type": "bot"}}

    record = await connector._transform_to_webpage_record(page, "page")

    assert record.last_modified_by == SourcePerson(source_id="bot-1", is_service_account=True)


async def test_page_without_people_names_no_one(connector) -> None:
    page = {k: v for k, v in PAGE.items() if k not in ("created_by", "last_edited_by")}

    record = await connector._transform_to_webpage_record(page, "page")

    assert record.authored_by is None
    assert record.last_modified_by is None


def test_a_partial_user_without_its_type_is_a_bot_when_the_user_sync_listed_it() -> None:
    """Notion's partial users often omit ``type``; a bot among them must not become a person node."""
    untyped = {"object": "user", "id": "bot-1"}
    assert notion_person(untyped, {"bot-1"}) == SourcePerson(source_id="bot-1", is_service_account=True)
    assert notion_person(untyped, {"other-bot"}).is_service_account is False
    assert notion_person({"object": "user", "id": "bot-2", "type": "bot"}).is_service_account is True


def test_a_page_created_by_a_known_bot_records_it_as_a_service_account(connector: NotionConnector) -> None:
    connector._bot_user_ids = {"bot-1"}
    person = notion_person({"object": "user", "id": "bot-1"}, connector._bot_user_ids)
    assert person is not None and person.is_service_account is True


def test_while_the_bot_list_is_incomplete_an_untyped_unknown_user_is_left_out() -> None:
    untyped = {"object": "user", "id": "someone"}
    assert notion_person(untyped, set(), bots_known=False) is None
    assert notion_person(untyped, set(), bots_known=True) == SourcePerson(source_id="someone", is_service_account=False)
    typed = {"object": "user", "id": "ann", "type": "person"}
    assert notion_person(typed, set(), bots_known=False) == SourcePerson(source_id="ann", is_service_account=False)

