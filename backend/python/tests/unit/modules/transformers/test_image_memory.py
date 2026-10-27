"""Indexing holds a bounded amount of memory for a record's images.

Each test here fails on the code before the fix: images were fetched all at
once at full size, kept at full size, the previous version of a record was held
through every vision call, the upload was built from several full copies of
the record, and nothing reserved memory for a record far bigger than the rest.
"""
from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import logging
import weakref
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.config.constants.arangodb import Connectors
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.models.entities import MimeTypes, OriginTypes, Record, RecordType
from app.modules.parsers.image_parser import image_parser as image_parser_module
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.blob_storage import BlobStorage, _json_utf8_bytes
from app.modules.transformers.sink_orchestrator import SinkOrchestrator, _fit_images
from app.modules.transformers.transformer import TransformContext
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.redis_streams.indexing_consumer import (
    IndexingRedisStreamsConsumer,
)
from app.services.resource_governor.models import ParseTier
from app.utils import image_utils
from app.utils.image_utils import (
    fit_image_for_index,
    fit_inline_images,
    read_image_dimensions,
)
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from app.events.processor import Processor

MB = 1024 * 1024
LOGGER = logging.getLogger("test-image-memory")


def _jpeg(width: int, height: int) -> str:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 200)).save(out, format="JPEG", quality=90)
    return f"data:image/jpeg;base64,{base64.b64encode(out.getvalue()).decode()}"


def _image_block(index: int, uri: str, **extra: str) -> Block:
    return Block(index=index, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": uri, **extra})


class TestFitImageForIndex:
    def test_a_photo_larger_than_indexing_keeps_is_scaled_to_2000_px(self) -> None:
        fitted = fit_image_for_index(_jpeg(4000, 3000))

        assert read_image_dimensions(fitted) == (2000, 1500)
        assert fitted.startswith("data:image/jpeg;base64,")

    def test_an_image_within_the_limits_is_kept_as_it_is(self) -> None:
        uri = _jpeg(800, 600)

        assert fit_image_for_index(uri) == uri


class TestInlineImages:
    def test_a_page_s_inline_photo_is_shrunk_in_place(self) -> None:
        photo = _jpeg(4000, 3000)

        page = fit_inline_images(f'<p>Before</p><img src="{photo}" alt="a"><p>After</p>')

        uri = page.split('src="', 1)[1].split('"', 1)[0]
        assert read_image_dimensions(uri) == (2000, 1500)
        assert page.startswith("<p>Before</p><img src=") and page.endswith('" alt="a"><p>After</p>')

    def test_a_page_whose_images_fit_is_left_as_it_is(self) -> None:
        page = f'![chart]({_jpeg(800, 600)}) and ![tiny](data:image/png;base64,iVBORw0KGgo=)'

        assert fit_inline_images(page) == page

    def test_past_the_record_budget_inline_images_are_taken_out(self, monkeypatch) -> None:
        photo = _jpeg(800, 600)
        monkeypatch.setattr(image_utils, "INDEX_RECORD_IMAGE_BYTES", len(photo))

        page = fit_inline_images(f'<img src="{photo}"><img src="{photo}">')

        assert page == f'<img src="{photo}"><img src="">'


class TestFetchingImages:
    async def test_no_more_than_four_images_are_downloaded_at_once(self) -> None:
        in_flight = peak = 0

        async def fetch(fetcher, url, logger=None) -> str:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return _jpeg(10, 10)

        with patch.object(ImageParser, "_fetch_single_url", side_effect=fetch):
            result = await ImageParser.urls_to_base64([f"https://example.com/{i}.jpg" for i in range(12)])

        assert len(result) == 12 and all(result)
        assert peak == 4

    async def test_each_image_is_shrunk_as_it_arrives(self) -> None:
        with patch.object(ImageParser, "_fetch_single_url", AsyncMock(return_value=_jpeg(4000, 3000))):
            [uri] = await ImageParser.urls_to_base64(["https://example.com/photo.jpg"])

        assert read_image_dimensions(uri) == (2000, 1500)

    async def test_past_the_record_budget_the_rest_are_not_downloaded(self, monkeypatch) -> None:
        small = _jpeg(10, 10)
        monkeypatch.setattr(image_parser_module, "INDEX_RECORD_IMAGE_BYTES", len(small))
        fetch = AsyncMock(return_value=small)

        with patch.object(ImageParser, "_fetch_single_url", fetch):
            result = await ImageParser.urls_to_base64([f"https://example.com/{i}.jpg" for i in range(8)])

        assert result[:4] == [small] * 4
        assert result[4:] == [None] * 4
        assert fetch.await_count == 4


class TestImagesInTheSink:
    def test_every_inline_image_is_shrunk_before_the_record_is_stored(self) -> None:
        block = _image_block(0, _jpeg(4000, 3000))

        assert _fit_images([block]) == 0

        assert read_image_dimensions(block.data["uri"]) == (2000, 1500)

    def test_past_the_record_budget_images_keep_their_text_but_not_their_pixels(self, monkeypatch) -> None:
        uri = _jpeg(100, 100)
        monkeypatch.setattr(sink_module, "INDEX_RECORD_IMAGE_BYTES", len(uri))
        blocks = [_image_block(i, uri, caption=f"figure {i}") for i in range(3)]

        assert _fit_images(blocks) == 2

        assert blocks[0].data == {"uri": uri, "caption": "figure 0"}
        assert [b.data for b in blocks[1:]] == [{"caption": "figure 1"}, {"caption": "figure 2"}]

    def test_an_image_that_is_not_inline_is_left_alone(self) -> None:
        block = _image_block(0, "https://example.com/photo.jpg")

        _fit_images([block])

        assert block.data == {"uri": "https://example.com/photo.jpg"}

    async def test_the_previous_version_is_not_held_through_the_descriptions(self) -> None:
        class _Stored(dict):
            pass

        previous = _Stored(block_containers={"blocks": []})
        held: list[bool] = []
        blob_storage = AsyncMock()
        blob_storage.get_record_from_storage = AsyncMock(return_value=previous)
        orchestrator = SinkOrchestrator(
            graphdb=AsyncMock(), blob_storage=blob_storage, vector_store=AsyncMock(),
            graph_provider=AsyncMock(), logger=LOGGER, config_service=MagicMock(),
        )
        previous_ref = weakref.ref(previous)
        del previous

        async def annotate(*_args, **_kwargs) -> int:
            blob_storage.get_record_from_storage.return_value = None
            gc.collect()
            held.append(previous_ref() is not None)
            return 0

        orchestrator.image_describer.annotate = annotate
        record = Record(
            id="rec-1", org_id="org-1", virtual_record_id="vr-1", record_name="page.html",
            record_type=RecordType.FILE, external_record_id="ext-1", version=1,
            origin=OriginTypes.CONNECTOR, connector_name=Connectors.KNOWLEDGE_BASE,
            connector_id="conn-1", mime_type=MimeTypes.HTML.value,
            block_containers=BlocksContainer(
                blocks=[_image_block(i, f"data:image/png;base64,AAAA{i}") for i in range(6)], block_groups=[],
            ),
        )

        await orchestrator._describe_images(TransformContext(record=record, settings={}))

        assert held == [False]


def _storage(**kwargs: object) -> BlobStorage:
    storage = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
    for name, value in kwargs.items():
        setattr(storage, name, value)
    return storage


def _record(size: int) -> dict:
    return {"id": "rec-1", "block_containers": {"blocks": [{"index": 0, "data": {"uri": "x" * size}}]}}


class TestUploadBody:
    def test_a_small_record_is_stored_as_plain_json(self) -> None:
        record = _record(100)

        body, compressed = _storage()._upload_body(record, "vr-1")

        assert compressed is False
        assert body == _json_utf8_bytes({"isCompressed": False, "record": record, "virtualRecordId": "vr-1"})

    @pytest.mark.parametrize("vrid", ["vr-1", None, "vr-ü", "vr-\udc00"])
    def test_a_large_record_gets_the_same_envelope_bytes_as_before(self, vrid, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES", "1000")
        storage = _storage()
        record = _record(5000)

        body, compressed = storage._upload_body(record, vrid)

        payload = storage._compress_record(record).decode("ascii")
        assert compressed is True
        assert body == _json_utf8_bytes({"isCompressed": True, "record": payload, "virtualRecordId": vrid})
        assert storage._process_downloaded_record(json.loads(body)) == record

    def test_a_record_that_fails_to_compress_is_stored_as_plain_json(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES", "1000")
        storage = _storage(_compress_record=MagicMock(side_effect=RuntimeError("zstd exploded")))
        record = _record(5000)

        body, compressed = storage._upload_body(record, "vr-1")

        assert compressed is False
        assert json.loads(body)["record"] == record

    async def test_a_cloud_upload_sends_the_bytes_it_reports(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES", "1000")
        storage = _storage()
        storage._get_auth_and_config = AsyncMock(return_value=({}, "http://node:3000", "s3"))
        storage._create_placeholder = AsyncMock(return_value={"_id": "doc-1"})
        storage._get_signed_url = AsyncMock(return_value={"signedUrl": "https://bucket/key"})
        storage._upload_to_signed_url = AsyncMock(return_value=200)
        record = _record(5000)

        with patch("app.modules.transformers.blob_storage.get_shared_session", return_value=MagicMock()):
            _, size = await storage.save_record_to_storage("org-1", "rec-1", "vr-1", record)

        sent = storage._upload_to_signed_url.await_args.args[2]
        assert isinstance(sent, bytes)
        assert size == len(sent)
        assert storage._process_downloaded_record(json.loads(sent)) == record

    async def test_the_signed_put_sends_bytes_not_a_dict_to_encode_again(self) -> None:
        response = AsyncMock()
        response.status = 200
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.put = MagicMock(return_value=response)

        await _storage()._upload_to_signed_url(session, "https://bucket/key?X-Amz-Signature=1", b'{"a": 1}')

        kwargs = session.put.call_args.kwargs
        assert kwargs["data"] == b'{"a": 1}'
        assert "json" not in kwargs
        assert kwargs["skip_auto_headers"] == {"Content-Type"}


def _governor_consumer() -> IndexingRedisStreamsConsumer:
    consumer = IndexingRedisStreamsConsumer(
        LOGGER,
        RedisStreamsConfig(host="localhost", port=6379, client_id="c", group_id="g", topics=["t"]),
        retry_manager=None,
        producer=None,
        governor=make_test_governor(logger_name="test-image-memory"),
    )
    consumer.running = True
    consumer._pending_message_is_owned = AsyncMock(return_value=True)
    consumer._ack_message = AsyncMock()
    consumer._requeue_message = AsyncMock()
    return consumer


def _fields() -> dict[str, str]:
    return {"value": json.dumps({"eventType": "newRecord", "payload": {"recordId": "r1", "extension": "html"}})}


def _page(size_bytes: int) -> "Callable[[object], AsyncGenerator[PipelineEvent, None]]":
    async def handler(_msg) -> AsyncGenerator[PipelineEvent, None]:
        yield PipelineEvent(
            event=IndexingEvent.START_PARSING,
            data=PipelineEventData(record_id="r1", tier=ParseTier.LIGHT, size_bytes=size_bytes),
        )
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

    return handler


class TestMemoryReservation:
    async def test_a_big_record_holds_its_reservation_until_it_is_indexed(self) -> None:
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        reserved: list[int] = []
        handler = _page(300 * MB)

        async def watching(msg) -> AsyncGenerator[PipelineEvent, None]:
            async for event in handler(msg):
                if event.event == IndexingEvent.INDEXING_COMPLETE:
                    reserved.append(budget.stats()["reserved_bytes"])
                yield event

        consumer.message_handler = watching

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is True

        assert reserved == [300 * MB * 4]
        assert budget.stats()["reserved_bytes"] == 0

    async def test_a_big_record_waits_for_memory_and_is_handed_back_without_an_attempt(self, monkeypatch) -> None:
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        budget.limit = 500 * MB
        assert await budget.reserve(400 * MB)
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)
        consumer.message_handler = _page(300 * MB)

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is False

        consumer._requeue_message.assert_awaited_once()
        assert budget.stats()["reserved_bytes"] == 400 * MB

    async def test_an_ordinary_record_never_waits_for_memory(self, monkeypatch) -> None:
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        budget.limit = 1 * MB
        assert await budget.reserve(1 * MB)
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)
        consumer.message_handler = _page(64 * 1024)

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is True

        consumer._requeue_message.assert_not_awaited()

    async def test_a_big_record_of_known_size_waits_for_memory_before_it_downloads(self, monkeypatch) -> None:
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        budget.limit = 500 * MB
        assert await budget.reserve(400 * MB)
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)
        downloaded: list[str] = []

        async def handler(_msg) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(
                event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id="r1", size_bytes=300 * MB),
            )
            downloaded.append("r1")
            async for event in _page(300 * MB)(_msg):
                yield event

        consumer.message_handler = handler

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is False

        assert downloaded == []
        consumer._requeue_message.assert_awaited_once()
        assert budget.stats()["reserved_bytes"] == 400 * MB

    async def test_a_record_reserved_before_its_download_is_not_charged_again_to_parse(self) -> None:
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        reserved: list[int] = []

        async def handler(_msg) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(
                event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id="r1", size_bytes=300 * MB),
            )
            async for event in _page(300 * MB)(_msg):
                if event.event == IndexingEvent.INDEXING_COMPLETE:
                    reserved.append(budget.stats()["reserved_bytes"])
                yield event

        consumer.message_handler = handler

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is True

        assert reserved == [300 * MB * 4]
        assert budget.stats()["reserved_bytes"] == 0

    async def test_a_memory_wait_is_not_counted_toward_the_delivery_backstop(self, monkeypatch) -> None:
        """A full budget says nothing about the record: ten such waits must not fail it."""
        consumer = _governor_consumer()
        tracker = AsyncMock()
        for name in ("get_count", "unfinished_attempts", "start_attempt"):
            getattr(tracker, name).return_value = 0
        tracker.record_delivery.return_value = 10
        consumer.retry_manager = tracker
        sink = MagicMock()
        sink.on_message_abandoned = AsyncMock()
        consumer.disposition_sink = sink
        budget = consumer.governor.memory_budget
        budget.limit = 500 * MB
        assert await budget.reserve(400 * MB)
        monkeypatch.setattr(concurrency, "parse_admission_wait_seconds", lambda: 0.05)

        async def handler(_msg) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(
                event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id="r1", size_bytes=300 * MB),
            )

        consumer.message_handler = handler

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is False

        tracker.record_delivery.assert_not_awaited()
        sink.on_message_abandoned.assert_not_awaited()
        consumer._requeue_message.assert_awaited_once()

    async def test_a_reservation_taken_before_the_download_grows_to_the_size_downloaded(self) -> None:
        """A document's sizeInBytes can be stale; the content downloaded is what is held."""
        consumer = _governor_consumer()
        budget = consumer.governor.memory_budget
        reserved: list[int] = []

        async def handler(_msg) -> AsyncGenerator[PipelineEvent, None]:
            yield PipelineEvent(
                event=IndexingEvent.START_DOWNLOAD, data=PipelineEventData(record_id="r1", size_bytes=100 * MB),
            )
            async for event in _page(300 * MB)(_msg):
                if event.event == IndexingEvent.INDEXING_COMPLETE:
                    reserved.append(budget.stats()["reserved_bytes"])
                yield event

        consumer.message_handler = handler

        assert await consumer._process_message_wrapper("t", "1-0", _fields()) is True

        assert reserved == [300 * MB * 4]
        assert budget.stats()["reserved_bytes"] == 0

    async def test_the_budget_is_the_free_memory_the_governor_measured(self) -> None:
        governor = make_test_governor(logger_name="test-image-memory-limit")

        # The test probe reports 8 GiB, 1 GiB of it in use.
        assert governor.memory_budget.limit == 7 * 1024 ** 3
        assert governor.stats()["record_memory"]["limit_bytes"] == 7 * 1024 ** 3


def _processor() -> "Processor":
    from app.events.processor import Processor

    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        processor = Processor(
            logger=MagicMock(), config_service=MagicMock(), indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(), parsers={}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    processor.graph_provider.get_document.return_value = None
    return processor


class TestParsingSeesShrunkImages:
    async def test_the_html_parser_never_sees_a_full_size_inline_photo(self) -> None:
        processor = _processor()
        seen: list[str] = []
        html_parser = MagicMock()
        html_parser.clean_html = MagicMock(side_effect=lambda text: seen.append(text) or text)
        html_parser.replace_relative_image_urls = MagicMock(side_effect=lambda text: text)
        html_parser.extract_and_replace_images = MagicMock(side_effect=lambda text: (text, []))
        html_parser.parse_to_blocks = AsyncMock(return_value=BlocksContainer(blocks=[], block_groups=[]))
        processor.parsers["html"] = html_parser
        page = f'<p>Photos</p><img src="{_jpeg(4000, 3000)}">'

        [_ async for _ in processor.process_html_document(
            "page.html", "rec-1", 1, "web", "org-1", page.encode(), "vr-1",
        )]

        [text] = seen
        assert len(text) < len(page) / 2
        assert read_image_dimensions(text.split('src="', 1)[1].split('"', 1)[0]) == (2000, 1500)

    async def test_the_markdown_parser_never_sees_a_full_size_inline_photo(self) -> None:
        processor = _processor()
        seen: list[str] = []
        md_parser = MagicMock()
        md_parser.extract_and_replace_images = MagicMock(side_effect=lambda text: seen.append(text) or (text, []))
        md_parser.parse_to_blocks = AsyncMock(return_value=BlocksContainer(blocks=[], block_groups=[]))
        processor.parsers["md"] = md_parser
        document = "# Photos\n\n![photo](" + _jpeg(4000, 3000) + ")\n"

        [_ async for _ in processor.process_md_document("doc.md", "rec-1", document.encode(), "vr-1")]

        [text] = seen
        assert len(text) < len(document) / 2
