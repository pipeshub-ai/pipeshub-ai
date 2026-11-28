"""PX-3 rung 1: parse work that ran on the event loop now runs on a thread.

Where a phase runs is asserted by the thread it runs on (exact, no timing); the tick-count tests
(T19) then prove the loop is actually free during a real parse: ticks are counted, not timed, and a
control run with the work put back on the loop shows the heartbeat does see the stall.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ExtensionTypes
from app.events import processor as processor_module
from app.models.entities import ProgressStatus  # noqa: F401  (kept for parity with sibling tests)
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.json import json_parser as json_parser_module
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.yaml import yaml_parser as yaml_parser_module
from app.modules.parsers.yaml.yaml_parser import YAMLParser
from app.services.parsing.interface import ParseError, ParseErrorCode

MAIN = threading.get_ident


# -- JSON -----------------------------------------------------------------------------------------


async def test_json_decode_runs_off_the_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """``decode_text(content)`` used to be evaluated as the *argument* of ``to_thread``, on the loop."""
    seen: list[int] = []
    original = json_parser_module.decode_text

    def recording(content: Any, **kwargs: Any) -> str:  # noqa: ANN401
        seen.append(threading.get_ident())
        return original(content, **kwargs)

    monkeypatch.setattr(json_parser_module, "decode_text", recording)

    result = await JSONParser().parse(json.dumps({"a": "b" * 50}).encode(), "r.json")

    assert result.block_container.blocks
    assert seen
    assert MAIN() not in seen


class _NoStrip(bytes):
    """Bytes whose ``strip`` fails: an emptiness test must not copy a payload that may be 50 MB."""

    def strip(self, *args: Any) -> bytes:  # noqa: ANN401
        raise AssertionError("strip() copies the payload")


@pytest.mark.parametrize("parser_cls", [JSONParser, YAMLParser])
async def test_emptiness_test_does_not_copy_the_payload(parser_cls: type) -> None:
    with pytest.raises(ParseError) as blank:
        await parser_cls().parse(_NoStrip(b" \n\t "), "r")
    assert blank.value.code == ParseErrorCode.EMPTY_CONTENT

    body = b'{"a": 1}' if parser_cls is JSONParser else b"a: 1\n"
    result = await parser_cls().parse(_NoStrip(body), "r")
    assert result.block_container.blocks


def test_isspace_and_strip_agree_on_every_byte() -> None:
    """The cheaper test must call exactly the payloads blank that ``not content.strip()`` did."""
    for value in range(256):
        content = bytes([value])
        assert (not content.strip()) == content.isspace(), value
    for content in (b"", b" a ", b"\xa0", b"\x00", b"\x0b\x0c"):
        assert (not content or not content.strip()) == (not content or content.isspace()), content


async def test_a_killed_worker_error_from_the_hop_reaches_the_retry_and_verdict_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T21. A parse worker's death raised from the decode-and-load step must reach
    ``worker_deaths.is_retrying_death`` and ``worker_verdict.worker_gave_up_on`` through ``parse()``:
    the step sits outside the ``except Exception`` that rewraps failures into a plain ``ParseError``
    (which would drop the worker's cause; both walk only ``__cause__`` and the error itself)."""
    from app.modules.parsers.parse_pool import ParseWorkerError
    from app.modules.parsers.worker_deaths import is_retrying_death
    from app.modules.parsers.worker_verdict import worker_gave_up_on

    def dies(_content: bytes) -> Any:  # noqa: ANN401
        raise ParseWorkerError("the parse worker was killed", "crashed", retrying=True)

    monkeypatch.setattr(json_parser_module, "_decode_and_load", dies)

    with pytest.raises(ParseError) as raised:
        await JSONParser().parse(b'{"a": 1}', "r.json")

    assert is_retrying_death(raised.value)
    assert worker_gave_up_on(raised.value)


async def test_an_ordinary_load_failure_is_still_wrapped_as_a_parse_failure() -> None:
    with pytest.raises(ParseError) as raised:
        await JSONParser().parse(b"{not json", "r.json")
    assert raised.value.code == ParseErrorCode.PARSE_FAILED
    assert "Failed to parse JSON for 'r.json'" in str(raised.value)


# -- YAML -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size_delta", "off_loop"),
    [(-1, False), (0, True), (1, True)],
    ids=["one-below-stays-on-loop", "at-threshold-hops", "one-above-hops"],
)
async def test_yaml_decode_hops_at_its_threshold(
    monkeypatch: pytest.MonkeyPatch, size_delta: int, off_loop: bool
) -> None:
    threshold = 64
    monkeypatch.setattr(yaml_parser_module, "YAML_DECODE_THRESHOLD_BYTES", threshold)
    seen: list[int] = []
    original = yaml_parser_module._decode_utf8

    def recording(content: bytes) -> str:
        seen.append(threading.get_ident())
        return original(content)

    monkeypatch.setattr(yaml_parser_module, "_decode_utf8", recording)
    body = b"k: " + b"x" * (threshold + size_delta - 4) + b"\n"
    assert len(body) == threshold + size_delta

    await YAMLParser().parse(body, "r.yaml")

    assert seen == [seen[0]]
    assert (seen[0] != MAIN()) is off_loop


async def test_yaml_decode_error_is_the_same_parse_error_after_the_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yaml_parser_module, "YAML_DECODE_THRESHOLD_BYTES", 1)
    with pytest.raises(ParseError) as raised:
        await YAMLParser().parse(b"a: \xff\xfe\n", "r.yaml")
    assert raised.value.code == ParseErrorCode.PARSE_FAILED
    assert "Failed to decode YAML for 'r.yaml'" in str(raised.value)
    assert isinstance(raised.value.__cause__, UnicodeDecodeError)


# -- HTML -----------------------------------------------------------------------------------------


class _RecordingSelectolax(SelectolaxHtmlParser):
    def __init__(self) -> None:
        super().__init__()
        self.threads: dict[str, int] = {}

    def clean_html(self, html_content: str) -> str:
        self.threads["clean_html"] = threading.get_ident()
        return super().clean_html(html_content)

    def replace_relative_image_urls(self, html_content: str) -> str:
        self.threads["replace_relative_image_urls"] = threading.get_ident()
        return super().replace_relative_image_urls(html_content)


class _Docling:
    """Stands in for the Docling backend: no ``PASSES_MAY_RUN_OFF_LOOP``."""

    def __init__(self) -> None:
        self.threads: dict[str, int] = {}

    def clean_html(self, html: str) -> str:
        self.threads["clean_html"] = threading.get_ident()
        return html

    def replace_relative_image_urls(self, html: str) -> str:
        self.threads["replace_relative_image_urls"] = threading.get_ident()
        return html


@pytest.mark.parametrize(("pad", "off_loop"), [(0, False), (10, True)], ids=["small-page-inline", "large-page-hops"])
async def test_html_passes_hop_above_their_threshold_for_selectolax(
    monkeypatch: pytest.MonkeyPatch, pad: int, off_loop: bool
) -> None:
    monkeypatch.setattr(processor_module, "HTML_CLEAN_THRESHOLD_BYTES", 2048)
    parser = _RecordingSelectolax()
    html = "<html><body><p>" + "w" * (pad * 1024) + "</p></body></html>"

    out = await processor_module._html_pass(parser, "clean_html", html)
    out = await processor_module._html_pass(parser, "replace_relative_image_urls", out)

    assert "<p>" in out
    assert {t != MAIN() for t in parser.threads.values()} == {off_loop}
    assert set(parser.threads) == {"clean_html", "replace_relative_image_urls"}


@pytest.mark.parametrize("pad", [0, 10])
async def test_html_pass_output_equals_the_direct_call(monkeypatch: pytest.MonkeyPatch, pad: int) -> None:
    monkeypatch.setattr(processor_module, "HTML_CLEAN_THRESHOLD_BYTES", 2048)
    parser = SelectolaxHtmlParser()
    html = (
        "<html><head><base href='https://e.com/d/'></head><body><nav>x</nav><script>1</script>"
        f"<p>é{'w' * (pad * 1024)}</p><img src='a.png'></body></html>"
    )
    assert await processor_module._html_pass(parser, "clean_html", html) == parser.clean_html(html)
    assert await processor_module._html_pass(
        parser, "replace_relative_image_urls", html
    ) == parser.replace_relative_image_urls(html)


async def test_html_passes_stay_on_the_loop_for_the_docling_backend_and_for_stand_ins() -> None:
    huge = "<p>" + "w" * (4 * 1024 * 1024) + "</p>"
    docling = _Docling()
    await processor_module._html_pass(docling, "clean_html", huge)
    await processor_module._html_pass(docling, "replace_relative_image_urls", huge)
    assert set(docling.threads.values()) == {MAIN()}

    standin = MagicMock()  # a MagicMock's attributes are truthy but are not the flag ``True``
    standin.clean_html = MagicMock(side_effect=lambda x: x)
    assert await processor_module._html_pass(standin, "clean_html", huge) == huge


def _processor() -> Any:  # noqa: ANN401
    from app.events.processor import Processor

    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        return Processor(
            logger=MagicMock(),
            config_service=AsyncMock(),
            indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(),
            parsers={},
            document_extractor=MagicMock(),
            sink_orchestrator=MagicMock(),
        )


async def _drain(gen: Any) -> list[Any]:  # noqa: ANN401
    return [e async for e in gen]


async def test_process_html_document_runs_both_passes_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both whole-document passes on the indexing path, through ``process_html_document``."""
    monkeypatch.setattr(processor_module, "HTML_CLEAN_THRESHOLD_BYTES", 1024)
    parser = _RecordingSelectolax()
    stop = RuntimeError("stop after the passes")
    parser.parse_to_blocks = AsyncMock(side_effect=stop)  # type: ignore[method-assign]
    proc = _processor()
    proc.parsers = {ExtensionTypes.HTML.value: parser}
    html = b"<html><body><p>" + b"w" * 4096 + b"</p></body></html>"

    with pytest.raises(Exception, match="stop after the passes"):
        await _drain(proc.process_html_document("a.html", "r1", "1", "web", "o1", html, "vr1"))

    assert set(parser.threads) == {"clean_html", "replace_relative_image_urls"}
    assert MAIN() not in parser.threads.values()


async def test_process_html_blockgroup_runs_clean_html_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.blocks import BlockGroup, DataFormat, GroupType

    monkeypatch.setattr(processor_module, "HTML_CLEAN_THRESHOLD_BYTES", 1024)
    parser = _RecordingSelectolax()
    proc = _processor()
    proc._group_parsers = MagicMock(return_value=(None, parser))  # type: ignore[method-assign]
    group = BlockGroup(
        index=0, type=GroupType.TEXT_SECTION, format=DataFormat.HTML, data="<p>" + "w" * 4096 + "</p>"
    )
    parser.parse_to_blocks = AsyncMock(side_effect=RuntimeError("stop"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="stop"):
        await proc._process_single_blockgroup_html(group, "r", allow_remote_fetch=False)

    assert parser.threads["clean_html"] != MAIN()


# -- dict payloads ---------------------------------------------------------------------------------


async def test_a_dict_payload_is_serialised_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    original = processor_module._dumps_payload

    def recording(payload: object) -> bytes:
        seen.append(threading.get_ident())
        return original(payload)

    monkeypatch.setattr(processor_module, "_dumps_payload", recording)
    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=ParseError(ParseErrorCode.EMPTY_CONTENT, "empty"))
    proc = _processor()
    proc.parsers = {"json": parser}
    proc._mark_record = AsyncMock()  # type: ignore[method-assign]

    await _drain(proc.process_structured_document("a.json", "r1", {"k": [1, 2, "é"]}, "vr1", "json"))

    sent = parser.parse.await_args.args[0]
    assert sent == json.dumps({"k": [1, 2, "é"]}, ensure_ascii=False).encode("utf-8")
    assert seen
    assert MAIN() not in seen


# -- the loop keeps ticking during a real parse (T19) --------------------------------------------------


async def _ticks_during(work: Any) -> int:  # noqa: ANN401
    count = 0
    running = True

    async def heartbeat() -> None:
        nonlocal count
        while running:
            await asyncio.sleep(0.001)
            count += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    count = 0
    try:
        await work()
    finally:
        running = False
        await beat
    return count


def _json_payload(megabytes: int) -> bytes:
    row = {"id": 0, "name": "n" * 20, "tags": ["a", "b", "c"], "score": 1.5, "note": "t" * 40}
    rows = []
    size = 0
    i = 0
    while size < megabytes * 1024 * 1024:
        entry = json.dumps({**row, "id": i})
        rows.append(entry)
        size += len(entry) + 2
        i += 1
    return ("[" + ", ".join(rows) + "]").encode()


async def test_loop_ticks_during_json_parse() -> None:
    """Characterisation: JSON loads were already threaded, so this passes on the code before PX-3 too.
    It guards the heartbeat from regressing, it does not prove the decode move."""
    payload = _json_payload(6)
    ticks = await _ticks_during(lambda: JSONParser().parse(payload, "big.json"))
    assert ticks >= 20, ticks


async def test_loop_ticks_during_yaml_parse() -> None:
    """Characterisation: the YAML load was already threaded, so this passes on the code before PX-3
    too; the decode hop itself is proved by ``test_yaml_decode_hops_at_its_threshold``."""
    payload = ("".join(f"- id: {i}\n  name: {'n' * 20}\n  tags: [a, b]\n" for i in range(12_000))).encode()
    assert len(payload) > 500_000
    ticks = await _ticks_during(lambda: YAMLParser().parse(payload, "big.yaml"))
    assert ticks >= 20, ticks


async def test_loop_ticks_during_html_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control (``clean_html`` called on the loop, as before) must show a much worse heartbeat."""
    parser = SelectolaxHtmlParser()
    html = "<html><body>" + "<div><p class='x'>paragraph text here</p><a href='/p'>link</a></div>" * 25_000 + "</body></html>"
    assert len(html) > 1_500_000

    async def off_loop() -> None:
        await processor_module._html_pass(parser, "clean_html", html)

    async def on_loop() -> None:
        parser.clean_html(html)

    with_hop = await _ticks_during(off_loop)
    control = await _ticks_during(on_loop)

    assert with_hop >= 20, with_hop
    assert control < with_hop / 4, (control, with_hop)


# -- the two other loop-resident HTML call sites (blocks parser, file-content parser) --------------


@pytest.mark.parametrize(("pad", "off_loop"), [(0, False), (8, True)], ids=["small-inline", "large-hops"])
async def test_blocks_parser_html_clean_runs_off_the_loop_only_when_large(
    monkeypatch: pytest.MonkeyPatch, pad: int, off_loop: bool
) -> None:
    from app.models.blocks import BlockGroup, DataFormat, GroupType
    from app.modules.parsers import loop_offload
    from app.modules.parsers.blocks.blocks_parser import BlocksParser

    monkeypatch.setattr(loop_offload, "HTML_CLEAN_THRESHOLD_BYTES", 2048)
    parser = _RecordingSelectolax()
    parser.parse_to_blocks = AsyncMock(side_effect=RuntimeError("stop"))  # type: ignore[method-assign]
    group = BlockGroup(
        index=0, type=GroupType.TEXT_SECTION, format=DataFormat.HTML, data="<p>" + "w" * (pad * 1024) + "</p>"
    )
    with pytest.raises(RuntimeError, match="stop"):
        await BlocksParser(MagicMock(), MagicMock())._process_single_blockgroup_html(
            group, "r", parser, allow_remote_fetch=False
        )
    assert (parser.threads["clean_html"] != MAIN()) is off_loop


@pytest.mark.parametrize(("pad", "off_loop"), [(0, False), (8, True)], ids=["small-inline", "large-hops"])
async def test_file_content_parser_html_passes_run_off_the_loop_only_when_large(
    monkeypatch: pytest.MonkeyPatch, pad: int, off_loop: bool
) -> None:
    from app.agents.actions.util.parse_file import FileContentParser
    from app.modules.parsers import loop_offload

    monkeypatch.setattr(loop_offload, "HTML_CLEAN_THRESHOLD_BYTES", 2048)
    fcp = object.__new__(FileContentParser)
    parser = _RecordingSelectolax()
    parser.parse_to_blocks = AsyncMock(side_effect=RuntimeError("stop"))  # type: ignore[method-assign]
    fcp._html_parser = parser
    fcp._image_parser = MagicMock()
    raw = ("<html><body><p>" + "w" * (pad * 1024) + "</p></body></html>").encode()
    with pytest.raises(RuntimeError, match="stop"):
        await fcp.handle_html(raw, "a.html")
    assert set(parser.threads) == {"clean_html", "replace_relative_image_urls"}
    assert {t != MAIN() for t in parser.threads.values()} == {off_loop}
