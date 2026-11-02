"""Indexing never fetches a remote image for content a caller pushed.

The push validates its body once; indexing then reads stored bytes. A group's
markdown or HTML can still name a remote image, so the guard has to hold at
index time on every path that parses it, with a spy on every way out of the
process that would fail the test the moment one is used.
"""

import ast
import json
import logging
import os
import pathlib
import subprocess
import sys
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ExtensionTypes
from app.connectors.sources.localKB.push.contract import PUSH_FORMAT, PUSH_ORIGIN_FIELD
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import IndexingError
from app.exceptions.non_retryable import NonRetryableError
from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.parsers.blocks.blocks_parser import BlocksParser
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.push_origin import (
    PushImageNotInlinedError,
    assert_images_inline,
    content_is_push_origin,
    is_push_origin,
    record_is_push_origin,
)

LOG = logging.getLogger("test")
PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="

# Remote references written the ways a scanner can miss them.
MARKDOWN_FORMS = [
    "![a](http://spy.test/a.png)",
    "![a]( http://spy.test/b.png )",
    "![a](http://spy.test/c.png 'T')",
    '![a](http://spy.test/d.png "T")',
    "![a](//spy.test/e.png)",
    "![a][r]\n\n   [r]: http://spy.test/f.png",
    '<img/src="http://spy.test/g.png">',
    '<IMG SRC="http://spy.test/h.png">',
    '![a](<http://spy.test/i.png>)',
]
HTML_FORMS = [
    '<img src="http://spy.test/a.png">',
    '<IMG SRC=http://spy.test/b.png>',
    '<img\nsrc="http://spy.test/c.png">',
    '<img/src="http://spy.test/d.png">',
    '<img srcset="http://spy.test/e.png 1x">',
    '<picture><source srcset="http://spy.test/f.png"><img src="http://spy.test/g.png"></picture>',
    '<img src="https://spy.test/h.png?x=1">',
]


class NetSpy:
    """Records every way out of the process; nothing is ever sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def record(self, kind: str, what: object = None) -> None:
        self.calls.append((kind, what))


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> NetSpy:
    import socket

    import aiohttp
    import httpx

    from app.utils import public_http, url_fetcher

    spy = NetSpy()

    async def fetcher_get(self, url, *a, **k) -> NoReturn:
        spy.record("PublicUrlFetcher.get", url)
        raise public_http.PublicFetchError("spy: no network")

    def fetch_url(url, *a, **k) -> NoReturn:
        spy.record("url_fetcher.fetch_url", url)
        raise url_fetcher.FetchError("spy: no network")

    async def aio_request(self, method, url, *a, **k) -> NoReturn:
        spy.record("aiohttp", url)
        raise aiohttp.ClientError("spy: no network")

    async def httpx_send(self, request, *a, **k) -> NoReturn:
        spy.record("httpx", str(request.url))
        raise httpx.ConnectError("spy: no network")

    def getaddrinfo(host, *a, **k) -> NoReturn:
        spy.record("getaddrinfo", host)
        raise socket.gaierror("spy: no network")

    monkeypatch.setattr(public_http.PublicUrlFetcher, "get", fetcher_get)
    monkeypatch.setattr(url_fetcher, "fetch_url", fetch_url)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", aio_request)
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx_send)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return spy


def _blocks(groups: list[BlockGroup], blocks: list[Block] | None = None, *, marker: bool = True) -> dict:
    doc = BlocksContainer(block_groups=groups, blocks=blocks or []).model_dump(mode="json")
    if marker:
        doc["push_format"] = PUSH_FORMAT
    return doc


def _group(i: int, data: str, fmt: DataFormat) -> BlockGroup:
    return BlockGroup(index=i, type=GroupType.TEXT_SECTION, requires_processing=True, data=data, format=fmt)


def _all_forms() -> dict:
    groups = [_group(i, f"intro\n\n{form}\n\nend", DataFormat.MARKDOWN) for i, form in enumerate(MARKDOWN_FORMS)]
    groups += [_group(len(groups) + i, f"<p>x</p>{form}", DataFormat.HTML) for i, form in enumerate(HTML_FORMS)]
    return _blocks(groups)


class _Pipeline:
    def __init__(self) -> None:
        self.applied: list[object] = []

    async def apply(self, ctx) -> None:
        self.applied.append(ctx)


def _processor(record: dict | Exception) -> tuple[Processor, _Pipeline, AsyncMock]:
    graph = AsyncMock()
    graph.get_document = AsyncMock(side_effect=record) if isinstance(record, Exception) else AsyncMock(return_value=record)
    parsers = {
        ExtensionTypes.PNG.value: ImageParser(LOG),
        ExtensionTypes.MD.value: MarkdownParser(logger=LOG, config_service=MagicMock()),
        ExtensionTypes.HTML.value: HTMLParser(logger=LOG, config_service=MagicMock()),
    }
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        proc = Processor(
            logger=LOG, config_service=MagicMock(), indexing_pipeline=MagicMock(), graph_provider=graph,
            parsers=parsers, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    pipeline = _Pipeline()
    proc._pipeline = lambda: pipeline  # type: ignore[method-assign]
    return proc, pipeline, graph


def _record(**over) -> dict:
    base = {
        "_key": "r1", "orgId": "o1", "recordName": "doc", "recordType": "FILE", "externalRecordId": "e1",
        "connectorId": "kb1", "mimeType": "application/vnd.pipeshub.blocks", "version": 1, "origin": "UPLOAD",
        "externalRevisionId": "other",
    }
    base.update(over)
    return base


async def _run_process_blocks(proc: Processor, doc: dict) -> list:
    with patch("app.events.processor.enhance_tables_with_llm", AsyncMock()):
        return [
            event
            async for event in proc.process_blocks(
                recordName="doc", recordId="r1", version=1, source="kb", orgId="o1",
                blocks_data=json.dumps(doc), virtual_record_id="v1",
            )
        ]


# ---------------------------------------------------------------- markers


class TestMarkers:
    def test_the_content_marker_is_the_key_the_push_writes(self) -> None:
        from app.connectors.sources.localKB.push.contract import Blocks, stored_bytes

        stored = json.loads(stored_bytes(Blocks.model_construct(block_groups=None, blocks=None), None))
        assert content_is_push_origin(stored)

    @pytest.mark.parametrize("doc", [{}, {"blocks": []}, None, "push_format", [("push_format", 1)]])
    def test_content_without_the_marker_is_not_push_origin(self, doc: object) -> None:
        assert not content_is_push_origin(doc)

    def test_each_marker_alone_is_enough(self) -> None:
        assert is_push_origin(content={"push_format": 1})
        assert is_push_origin(record={PUSH_ORIGIN_FIELD: True})
        assert is_push_origin(record={"externalRevisionId": "pb1:" + "a" * 64 + ":" + "b" * 16})
        assert is_push_origin(record={"externalRevisionId": "pb7:x"})

    def test_an_ordinary_record_is_not_push_origin(self) -> None:
        assert not is_push_origin(content={"blocks": []}, record={"externalRevisionId": "123", PUSH_ORIGIN_FIELD: False})
        assert not is_push_origin()
        assert not record_is_push_origin(MagicMock())

    def test_the_indexing_record_converter_carries_the_flag(self) -> None:
        from app.events.processor import convert_record_dict_to_record

        base = _record(recordName="n", orgId="o", connectorId="kb")
        assert convert_record_dict_to_record({**base, PUSH_ORIGIN_FIELD: True}).push_origin is True
        assert convert_record_dict_to_record(base).push_origin is False

    def test_the_stored_record_schema_accepts_the_flag_and_no_other_value(self) -> None:
        from app.schema.arango.documents import record_schema

        prop = record_schema["rule"]["properties"]["pushOrigin"]
        assert prop == {"type": "boolean"}
        assert record_schema["rule"].get("additionalProperties") is False

    def test_the_flag_survives_the_record_entity_both_ways(self) -> None:
        from app.models.entities import FileRecord, Record

        base = _record(createdAtTimestamp=1, updatedAtTimestamp=1)
        assert Record.from_arango_base_record(base).push_origin is False
        flagged = Record.from_arango_base_record({**base, PUSH_ORIGIN_FIELD: True})
        assert flagged.push_origin is True
        assert flagged.to_arango_base_record()[PUSH_ORIGIN_FIELD] is True
        assert PUSH_ORIGIN_FIELD not in Record.from_arango_base_record(base).to_arango_base_record()
        file_rec = FileRecord.from_arango_record({}, {**base, PUSH_ORIGIN_FIELD: True})
        assert file_rec.push_origin is True


# ---------------------------------------------------------------- the guard at every entry


class TestProcessBlocksNeverFetches:
    @pytest.mark.parametrize(
        ("name", "doc_marker", "record"),
        [
            ("content marker only", True, _record()),
            ("record flag only", False, _record(**{PUSH_ORIGIN_FIELD: True})),
            ("legacy revision tag only", False, _record(externalRevisionId="pb1:" + "a" * 64 + ":" + "b" * 16)),
        ],
    )
    async def test_zero_connections_for_every_marker(self, net: NetSpy, name: str, doc_marker: bool, record: dict) -> None:
        doc = _all_forms()
        if not doc_marker:
            doc.pop("push_format")
        proc, pipeline, _ = _processor(record)

        await _run_process_blocks(proc, doc)

        assert net.calls == [], name
        assert len(pipeline.applied) == 1

    async def test_the_same_record_is_indexed_again_without_fetching(self, net: NetSpy) -> None:
        proc, _, _ = _processor(_record())
        for _ in range(2):
            await _run_process_blocks(proc, _all_forms())
        assert net.calls == []

    async def test_control_an_ordinary_record_still_fetches(self, net: NetSpy) -> None:
        doc = _all_forms()
        doc.pop("push_format")
        proc, _, _ = _processor(_record())

        await _run_process_blocks(proc, doc)

        assert any(kind == "PublicUrlFetcher.get" for kind, _ in net.calls), "the spy cannot see a fetch"

    async def test_inline_images_of_a_pushed_group_survive(self, net: NetSpy) -> None:
        doc = _blocks([_group(0, f"![logo]({PIXEL})\n\n![far](http://spy.test/x.png)", DataFormat.MARKDOWN)])
        proc, pipeline, _ = _processor(_record())

        await _run_process_blocks(proc, doc)

        images = [b for b in pipeline.applied[0].record.block_containers.blocks if b.type == BlockType.IMAGE]
        assert [b.data["uri"] for b in images] == [PIXEL]
        assert net.calls == []

    async def test_a_failed_record_read_is_retryable_and_drops_nothing(self, net: NetSpy) -> None:
        proc, pipeline, _ = _processor(ConnectionError("graph down"))

        with pytest.raises(ConnectionError):
            await _run_process_blocks(proc, _all_forms())

        assert pipeline.applied == [] and net.calls == []
        assert not issubclass(ConnectionError, NonRetryableError)

    async def test_a_remote_uri_in_a_pushed_record_fails_it_without_retry(self, net: NetSpy) -> None:
        seeded = Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "http://spy.test/x.png"})
        doc = _blocks([], [seeded])
        proc, pipeline, _ = _processor(_record())

        with pytest.raises(PushImageNotInlinedError) as exc:
            await _run_process_blocks(proc, doc)

        assert isinstance(exc.value, IndexingError) and isinstance(exc.value, NonRetryableError)
        assert pipeline.applied == [] and net.calls == []

    async def test_the_record_read_asks_the_store_to_raise_so_an_outage_is_retried(self, net: NetSpy) -> None:
        """Without the flag both graph providers answer None on an error, which reads as a deleted record."""
        proc, _, graph = _processor(_record())

        async def honour(rid: str, collection: str, raise_on_error: bool = False) -> dict | None:
            return _record() if raise_on_error else None

        graph.get_document.side_effect = honour

        await _run_process_blocks(proc, _blocks([]))

        assert graph.get_document.await_args.kwargs.get("raise_on_error") is True

    async def test_a_remote_uri_in_an_ordinary_record_is_left_alone(self, net: NetSpy) -> None:
        seeded = Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "http://spy.test/x.png"})
        doc = _blocks([], [seeded], marker=False)
        proc, pipeline, _ = _processor(_record())

        await _run_process_blocks(proc, doc)

        assert len(pipeline.applied) == 1


class TestBlocksParserNeverFetches:
    def _parser(self) -> BlocksParser:
        return BlocksParser(LOG, MagicMock())

    async def test_zero_connections_with_the_content_marker(self, net: NetSpy) -> None:
        with patch("app.modules.parsers.blocks.blocks_parser.enhance_tables_with_llm", AsyncMock()):
            await self._parser().parse(json.dumps(_all_forms()).encode(), "doc")
        assert net.calls == []

    async def test_control_unmarked_content_still_fetches(self, net: NetSpy) -> None:
        doc = _all_forms()
        doc.pop("push_format")
        with patch("app.modules.parsers.blocks.blocks_parser.enhance_tables_with_llm", AsyncMock()):
            await self._parser().parse(json.dumps(doc).encode(), "doc")
        assert any(kind == "PublicUrlFetcher.get" for kind, _ in net.calls)

    async def test_a_remote_uri_fails_pushed_content(self, net: NetSpy) -> None:
        seeded = Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "https://spy.test/x.png"})
        with pytest.raises(PushImageNotInlinedError):
            await self._parser().parse(json.dumps(_blocks([], [seeded])).encode(), "doc")


class TestTheSinkItself:
    async def test_a_remote_url_is_not_fetched_when_not_allowed(self, net: NetSpy) -> None:
        out = await ImageParser.urls_to_base64(["http://spy.test/a.png", PIXEL, "//spy.test/b.png", "DATA:image/png;base64,AA=="], allow_remote_fetch=False)
        assert out[0] is None and out[1] == PIXEL and out[2] is None and out[3] is None
        assert net.calls == []

    async def test_the_keyword_has_no_default(self) -> None:
        with pytest.raises(TypeError):
            await ImageParser.urls_to_base64(["http://spy.test/a.png"])  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await ImageParser._fetch_single_url(None, "http://spy.test/a.png")  # type: ignore[call-arg]


# ---------------------------------------------------------------- the parser choice


class TestPushedContentTakesTheLightweightParsers:
    async def test_a_docling_backend_is_never_called_for_pushed_groups(self, net: NetSpy) -> None:
        proc, _, _ = _processor(_record())
        docling_md, docling_html = MagicMock(), MagicMock()
        proc.parsers[ExtensionTypes.MD.value] = docling_md
        proc.parsers[ExtensionTypes.HTML.value] = docling_html

        await _run_process_blocks(proc, _all_forms())

        assert docling_md.mock_calls == [] and docling_html.mock_calls == []

    def test_the_group_parsers_are_the_lightweight_classes(self) -> None:
        proc, _, _ = _processor(_record())
        md, html = proc._group_parsers(allow_remote_fetch=False)
        assert type(md).__name__ == "MarkdownItParser" and type(html).__name__ == "SelectolaxHtmlParser"

    def test_the_selector_imports_no_docling_class_whatever_the_backend(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[4]
        code = (
            f"import sys; sys.path.insert(0, {str(root)!r})\n"
            "from app.modules.parsers.lightweight_group_parsers import lightweight_group_parsers\n"
            "md, html = lightweight_group_parsers()\n"
            "assert type(md).__name__ == 'MarkdownItParser', type(md)\n"
            "assert type(html).__name__ == 'SelectolaxHtmlParser', type(html)\n"
            "bad = [m for m in sys.modules if m.startswith('app.') and 'docling' in m]\n"
            "assert not bad, bad\n"
        )
        done = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=240,
            env={**os.environ, "PARSER_BACKEND": "docling"}, cwd=root,
        )
        assert done.returncode == 0, done.stderr[-2000:]

    def test_the_push_origin_modules_name_no_docling_class(self) -> None:
        import app.modules.parsers.lightweight_group_parsers as selector
        import app.modules.parsers.push_origin as markers

        for module in (selector, markers):
            names = [
                n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else getattr(n, "module", "") or ""
                for n in ast.walk(ast.parse(pathlib.Path(module.__file__).read_text()))
            ]
            assert not [n for n in names if "docling" in n.lower()]


class TestAssertImagesInline:
    def test_data_uris_and_empty_images_pass(self) -> None:
        container = BlocksContainer(blocks=[
            Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": PIXEL}),
            Block(index=1, type=BlockType.IMAGE, format=DataFormat.BASE64, data=None),
            Block(index=2, type=BlockType.TEXT, format=DataFormat.TXT, data="http://not-an-image-block"),
        ])
        assert_images_inline(container)

    @pytest.mark.parametrize("uri", ["http://x.test/a.png", "https://x.test/a.png", "//x.test/a.png", "DATA:image/png;base64,AA==", "data:text/html;base64,AA==", 5, ""])
    def test_anything_else_fails(self, uri: object) -> None:
        container = BlocksContainer(blocks=[Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": uri})])
        with pytest.raises(PushImageNotInlinedError):
            assert_images_inline(container)
