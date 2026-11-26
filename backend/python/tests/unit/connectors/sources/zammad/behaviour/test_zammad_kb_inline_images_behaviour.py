"""A knowledge-base answer's text inlines only its own images.

The token that syncs can read attachments the answer's readers can't, and an
``<img>`` tag may name any attachment id on any host. So an image is inlined (as a
data URI) only when it is one of the answer's own attachments, its URL is relative
or on the configured Zammad host, and it is no larger than MAX_INLINE_IMAGE_BYTES.
Anything else stays a link and is never fetched.

``stream_record`` of the answer runs as in production; Zammad is a fake data
source and our store the in-memory ``FakeRecordsDb``.
"""

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeRecordsDb,
    FakeStore,
    epoch_ms,
    iso,
)

from app.connectors.sources.zammad import connector as zammad_connector
from app.connectors.sources.zammad.connector import ZammadConnector
from app.models.entities import RecordType, WebpageRecord
from app.sources.client.zammad.zammad import ZammadResponse

ANSWER, CONTENT = 5, 2005
LIMIT = 1_000  # MAX_INLINE_IMAGE_BYTES for these tests
PNG = b"\x89PNG\r\n" + b"x" * 10
OWN, HUGE_STATED, HUGE_ACTUAL, UNSIZED, FOREIGN = 7, 8, 9, 10, 99


class FakeKbSource:
    """The knowledge-base calls a stream makes, answering for one answer and its attachments."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.attachment_reads: list[int] = []
        self.answer_reads: list[dict[str, Any]] = []
        self.attachments = [
            {"id": OWN, "filename": "own.png", "size": len(PNG), "preferences": {"Content-Type": "image/png"}},
            {"id": HUGE_STATED, "filename": "huge.png", "size": LIMIT + 1, "preferences": {"Content-Type": "image/png"}},
            {"id": HUGE_ACTUAL, "filename": "liar.png", "size": 10, "preferences": {"Content-Type": "image/png"}},
            {"id": UNSIZED, "filename": "unsized.png", "preferences": {"Content-Type": "image/png"}},
        ]

    async def init_knowledge_base(self) -> ZammadResponse:
        # A second translation: its content id is not the answer id.
        return ZammadResponse(success=True, status_code=200, data={
            "KnowledgeBase": {"1": {"id": 1}},
            "KnowledgeBaseAnswerTranslation": {
                "1005": {"id": 1005, "answer_id": ANSWER, "content_id": CONTENT},
                "1006": {"id": 1006, "answer_id": 6, "content_id": 2006},
            },
        })

    async def get_kb_answer(self, id: int, kb_id: int | None = None,  # noqa: A002 - mirrors the real signature
                            content_id: int | None = None) -> ZammadResponse:
        self.answer_reads.append({"id": id, "kb_id": kb_id, "content_id": content_id})
        return ZammadResponse(success=True, status_code=200, data={"assets": {
            "KnowledgeBaseAnswer": {str(ANSWER): {
                "id": ANSWER, "category_id": 1, "translation_ids": [1005], "attachments": list(self.attachments),
                "published_at": iso(epoch_ms(1)), "updated_at": iso(epoch_ms(1)),
            }},
            "KnowledgeBaseAnswerTranslation": {"1005": {"id": 1005, "title": "How to", "content_id": CONTENT}},
            "KnowledgeBaseAnswerTranslationContent": {str(CONTENT): {"id": CONTENT, "body": self.body}},
        }})

    async def get_kb_answer_attachment(self, id: int) -> ZammadResponse:  # noqa: A002
        self.attachment_reads.append(id)
        data = b"\x89PNG" + b"x" * (LIMIT + 10) if id == HUGE_ACTUAL else PNG
        return ZammadResponse(success=True, status_code=200, data=data)

    async def get_kb_category_permissions(self, kb_id: int, cat_id: int) -> ZammadResponse:
        return ZammadResponse(success=True, status_code=200, data={"roles_editor": [], "roles_reader": [], "permissions": []})


def _answer_record() -> WebpageRecord:
    return WebpageRecord(
        id="answer-node", org_id="org-1", record_name="KB answer 5", record_type=RecordType.WEBPAGE,
        external_record_id=f"kb_answer_{ANSWER}", external_record_group_id="cat_1", version=0,
        origin="CONNECTOR", connector_name="ZAMMAD", connector_id="zm-images",
        weburl="https://zammad.test/help/kb_answer_5", source_created_at=epoch_ms(1), source_updated_at=epoch_ms(1),
        inherit_permissions=False,
    )


async def _stream(body: str, monkeypatch: pytest.MonkeyPatch) -> tuple[str, FakeKbSource]:
    monkeypatch.setattr(zammad_connector, "MAX_INLINE_IMAGE_BYTES", LIMIT)
    source = FakeKbSource(body)
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=logging.getLogger("test.zammad.images"),
            data_entities_processor=FakeRecordsDb(),
            data_store_provider=FakeStore(),
            config_service=FakeConfigService(),
            connector_id="zm-images",
            scope="team",
            created_by="admin",
        )
    connector.base_url = "https://zammad.test"
    connector.data_source = source
    connector._get_fresh_datasource = AsyncMock(return_value=source)
    response = await connector.stream_record(_answer_record())
    container = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
    return container["block_groups"][0]["data"], source


def _inlined(text: str) -> int:
    return text.count("data:image/png;base64,")


async def test_the_answers_own_image_is_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    text, source = await _stream(f'<p>See <img src="/api/v1/attachments/{OWN}"></p>', monkeypatch)

    assert _inlined(text) == 1
    assert source.attachment_reads == [OWN]


async def test_an_image_on_the_configured_host_is_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    text, _ = await _stream(f'<img src="https://ZAMMAD.test/api/v1/attachments/{OWN}?view=inline">', monkeypatch)

    assert _inlined(text) == 1


async def test_another_answers_attachment_is_neither_fetched_nor_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    text, source = await _stream(f'<img src="/api/v1/attachments/{FOREIGN}">', monkeypatch)

    assert _inlined(text) == 0
    assert source.attachment_reads == []
    assert f"/api/v1/attachments/{FOREIGN}" in text


@pytest.mark.parametrize("src", [
    f"https://evil.example/api/v1/attachments/{OWN}",
    f"http://zammad.test/api/v1/attachments/{OWN}",
    f"https://zammad.test:8443/api/v1/attachments/{OWN}",
    f"//evil.example/api/v1/attachments/{OWN}",
])
async def test_an_image_on_another_host_is_neither_fetched_nor_inlined(
    monkeypatch: pytest.MonkeyPatch, src: str,
) -> None:
    text, source = await _stream(f'<img src="{src}">', monkeypatch)

    assert _inlined(text) == 0
    assert source.attachment_reads == []


async def test_an_image_stated_larger_than_the_limit_is_not_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    text, source = await _stream(f'<img src="/api/v1/attachments/{HUGE_STATED}">', monkeypatch)

    assert _inlined(text) == 0
    assert source.attachment_reads == []


async def test_an_image_larger_than_the_limit_when_fetched_is_not_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    text, source = await _stream(f'<img src="/api/v1/attachments/{HUGE_ACTUAL}">', monkeypatch)

    assert _inlined(text) == 0
    assert source.attachment_reads == [HUGE_ACTUAL]


async def test_an_image_with_no_stated_size_is_inlined_within_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    text, _ = await _stream(f'<img src="/api/v1/attachments/{UNSIZED}">', monkeypatch)

    assert _inlined(text) == 1


async def test_only_the_allowed_images_of_a_mixed_body_are_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        f'<img src="/api/v1/attachments/{OWN}"><img src="/api/v1/attachments/{FOREIGN}">'
        f'<img src="https://evil.example/api/v1/attachments/{OWN}"><img src="/api/v1/attachments/{HUGE_STATED}">'
    )

    text, source = await _stream(body, monkeypatch)

    assert _inlined(text) == 1
    assert source.attachment_reads == [OWN]


async def test_the_body_is_asked_for_by_the_translations_content_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _, source = await _stream("<p>text</p>", monkeypatch)

    assert source.answer_reads == [{"id": ANSWER, "kb_id": 1, "content_id": CONTENT}]
