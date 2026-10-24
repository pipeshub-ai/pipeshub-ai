"""A deleted BookStack chapter leaves both graphs with its pages; its book stays.

Found in a live run of PR #3880 (BS-11): BookStack logs no page_delete for the
pages of a deleted chapter, and the chapter delete handler was a stub, so the
chapter and its pages stayed listed after an incremental sync.
"""

import logging
import random
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType
from app.sources.client.bookstack.bookstack import BookStackResponse

from ..processor_harness import build_processor
from ..write_path_support import ORG, _seed_app, _suffix

pytestmark = pytest.mark.integration


def _group(external_id: str, connector_id: str, kind: RecordGroupType, parent: str | None = None) -> RecordGroup:
    return RecordGroup(
        org_id=ORG, name=external_id, external_group_id=external_id, connector_name=Connectors.BOOKSTACK,
        connector_id=connector_id, group_type=kind, parent_external_group_id=parent, inherit_permissions=parent is not None,
    )


def _page(external_id: str, connector_id: str, group: str) -> FileRecord:
    return FileRecord(
        id=str(uuid.uuid4()), org_id=ORG, record_name=external_id, record_type=RecordType.FILE,
        external_record_group_id=group, external_record_id=external_id, version=0,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.BOOKSTACK, connector_id=connector_id,
        is_file=True, extension="md", mime_type="text/markdown",
    )


async def test_a_deleted_chapter_takes_its_pages_and_leaves_its_book(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id, name="BookStack", app_type=Connectors.BOOKSTACK.value, app_group="BookStack")
    chapter_id = random.randint(10_000, 99_999_999)
    book, chapter = f"book/{chapter_id + 1}", f"chapter/{chapter_id}"
    await processor.on_new_record_groups([
        (_group(book, connector_id, RecordGroupType.BOOK), []),
        (_group(chapter, connector_id, RecordGroupType.CHAPTER, parent=book), []),
    ])
    in_chapter = [_page(f"page/{_suffix()}", connector_id, chapter) for _ in range(2)]
    in_book = _page(f"page/{_suffix()}", connector_id, book)
    await processor.on_new_records([(p, []) for p in [*in_chapter, in_book]])

    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        connector = BookStackConnector(
            logger=logging.getLogger("bookstack-it"), data_entities_processor=processor,
            data_store_provider=MagicMock(), config_service=AsyncMock(), connector_id=connector_id,
            scope="team", created_by="admin",
        )
    connector.data_source = MagicMock()
    connector.data_source.list_chapters = AsyncMock(return_value=BookStackResponse(success=True, data={"data": [], "total": 0}))

    done = await connector._handle_record_group_delete_event({"loggable_id": chapter_id, "loggable_type": "chapter"}, "chapter")

    assert done is True
    assert await backend.provider.get_record_group_by_external_id(connector_id, chapter) is None
    for page in in_chapter:
        assert await backend.provider.get_record_by_external_id(connector_id, page.external_record_id) is None
    assert await backend.provider.get_record_group_by_external_id(connector_id, book) is not None
    assert await backend.provider.get_record_by_external_id(connector_id, in_book.external_record_id) is not None
