"""How ``Processor.process_code_document`` reads a file from a code repository.

A file with no source grammar used to be parsed whole as Markdown, whatever it
was and however large. These tests pin what happens instead: data files go to
the parser for their format, generated files are skipped, the text fallback is
capped, and the event loop keeps turning while a large file is parsed.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ExtensionTypes, ProgressStatus
from app.events import processor as processor_module
from app.events.processor import Processor
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.parsers.code_parser import engine
from app.modules.parsers.code_parser.code_file_parser import CodeFileParser
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.excel.prompt_template import CSVHeaderDetection
from app.services.messaging.config import IndexingEvent
from app.services.parsing.interface import ParseResult

if TYPE_CHECKING:
    from app.modules.transformers.transformer import TransformContext

RECORD = {
    "_key": "rec-1",
    "orgId": "org-1",
    "recordName": "file",
    "recordType": "CODE_FILE",
    "indexingStatus": "IN_PROGRESS",
    "version": 1,
    "origin": "CONNECTOR",
    "connectorName": "GITLAB",
    "connectorId": "conn-1",
    "externalRecordId": "ext-1",
    "mimeType": "text/plain",
}
DONE = [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]


def _one_block() -> BlocksContainer:
    return BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, data="hello", format=DataFormat.TXT)]
    )


class _Pipeline:
    """Stands in for the indexing pipeline: these tests stop at the parser."""

    applied: list[TransformContext] = []

    def __init__(self, **_: object) -> None:
        pass

    async def apply(self, ctx: TransformContext) -> None:
        _Pipeline.applied.append(ctx)


class Harness:
    def __init__(self) -> None:
        self.md = MagicMock(name="markdown parser")
        self.md.extract_and_replace_images = MagicMock(side_effect=lambda text: (text, []))
        self.md.parse_to_blocks = AsyncMock(return_value=_one_block())

        self.csv = MagicMock(name="csv parser")
        self.csv.read_raw_rows = MagicMock(return_value=[["a", "b"], ["1", "2"]])
        self.csv.find_tables_in_csv = MagicMock(
            return_value=[{"raw_rows": [["a", "b"], ["1", "2"]], "start_row": 1, "end_row": 2}]
        )
        self.csv.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=_one_block())
        self.tsv = MagicMock(name="tsv parser")
        self.tsv.read_raw_rows = MagicMock(return_value=[["a", "b"], ["1", "2"]])
        self.tsv.find_tables_in_csv = MagicMock(return_value=[])
        self.tsv.get_blocks_from_csv_with_multiple_tables = AsyncMock(return_value=_one_block())

        self.json = MagicMock(name="json parser")
        self.json.parse = AsyncMock(return_value=ParseResult(block_container=_one_block()))
        self.yaml = MagicMock(name="yaml parser")
        self.yaml.parse = AsyncMock(return_value=ParseResult(block_container=_one_block()))

        self.code = MagicMock(name="code parser")
        self.code.parse_to_blocks = MagicMock(return_value=_one_block())
        self.code.parse_to_blocks_off_loop = AsyncMock(return_value=_one_block())

        self.graph = AsyncMock()
        self.graph.get_document.return_value = dict(RECORD)
        self.graph.update_node.return_value = True
        self.logger = MagicMock()

        parsers = {
            ExtensionTypes.MD.value: self.md,
            ExtensionTypes.CSV.value: self.csv,
            ExtensionTypes.TSV.value: self.tsv,
            ExtensionTypes.JSON.value: self.json,
            ExtensionTypes.YAML.value: self.yaml,
            ExtensionTypes.CODE.value: self.code,
        }
        with patch.object(processor_module, "DoclingClient"), \
             patch.object(processor_module, "DoclingProcessor"):
            self.processor = Processor(
                logger=self.logger,
                config_service=MagicMock(),
                indexing_pipeline=MagicMock(),
                graph_provider=self.graph,
                parsers=parsers,
                document_extractor=MagicMock(),
                sink_orchestrator=MagicMock(),
            )
        self.processor._get_llm_for_role = AsyncMock(return_value=(MagicMock(), None))

    async def run(self, name: str, content: bytes, extension: str | None = None) -> list:
        if extension is None and "." in name:
            extension = name.rsplit(".", 1)[-1].lower()
        with patch.object(processor_module, "IndexingPipeline", _Pipeline):
            return [
                event.event
                async for event in self.processor.process_code_document(
                    recordName=name,
                    recordId="rec-1",
                    code_binary=content,
                    virtual_record_id="vr-1",
                    extension=extension,
                    file_path=f"repo/{name}",
                )
            ]

    def status_writes(self) -> list[dict]:
        return [call.args[2] for call in self.graph.update_node.call_args_list]

    def parsed_as_markdown(self) -> bool:
        return self.md.parse_to_blocks.await_count > 0

    def parsed_as_code(self) -> bool:
        return (
            self.code.parse_to_blocks.call_count + self.code.parse_to_blocks_off_loop.await_count
        ) > 0

    def log_lines(self) -> str:
        return "\n".join(str(call.args[0]) for call in self.logger.info.call_args_list)


@pytest.fixture
def harness() -> Harness:
    _Pipeline.applied = []
    return Harness()


# -- data files go to the parser for their format ----------------------------


async def test_a_csv_in_a_repository_is_read_by_the_csv_parser(harness: Harness) -> None:
    events = await harness.run("customers.csv", b"a,b\n1,2\n")

    assert events == DONE
    harness.csv.read_raw_rows.assert_called_once()
    harness.csv.get_blocks_from_csv_with_multiple_tables.assert_awaited_once()
    assert not harness.parsed_as_markdown()
    assert not harness.parsed_as_code()


async def test_a_tsv_in_a_repository_is_read_by_the_tsv_parser(harness: Harness) -> None:
    await harness.run("customers.tsv", b"a\tb\n1\t2\n")

    harness.tsv.read_raw_rows.assert_called_once()
    harness.csv.read_raw_rows.assert_not_called()
    assert not harness.parsed_as_markdown()


async def test_a_json_file_in_a_repository_is_read_by_the_json_parser(harness: Harness) -> None:
    events = await harness.run("fixtures.json", b'{"name": "x"}')

    assert events == DONE
    harness.json.parse.assert_awaited_once()
    assert harness.json.parse.await_args.args[0] == b'{"name": "x"}'
    assert not harness.parsed_as_markdown()


@pytest.mark.parametrize("name", ["values.yaml", "values.yml"])
async def test_a_yaml_file_in_a_repository_is_read_by_the_yaml_parser(
    harness: Harness, name: str
) -> None:
    await harness.run(name, b"name: x\n")

    harness.yaml.parse.assert_awaited_once()
    assert not harness.parsed_as_markdown()


async def test_a_repository_csv_gets_the_csv_parsers_own_limits_not_the_code_limit(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row limit an uploaded CSV gets applies; the code size limit does not."""
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 16)
    monkeypatch.setenv("MAX_TABLE_ROWS_FOR_LLM", "3")
    parser = CSVParser(config_service=MagicMock())
    parser.detect_headers_with_llm = AsyncMock(
        return_value=CSVHeaderDetection(
            has_headers=True, num_header_rows=1, confidence="high", reasoning="first row"
        )
    )
    parser.get_table_summary = AsyncMock(return_value="six customers")
    parser.get_rows_text = AsyncMock(return_value=[])
    harness.processor.parsers[ExtensionTypes.CSV.value] = parser
    rows = "".join(f"{i},customer {i}\n" for i in range(6))

    events = await harness.run("customers.csv", ("id,name\n" + rows).encode())

    assert events == DONE
    assert not harness.parsed_as_markdown()
    # Six rows is over the limit of three, so no row was sent to the LLM.
    parser.get_rows_text.assert_not_awaited()
    container = _Pipeline.applied[0].record.block_containers
    assert [b.data["row_natural_language_text"] for b in container.blocks] == [
        f"id: {i}, name: customer {i}" for i in range(6)
    ]


# -- generated and unreadable files are skipped, with a reason ----------------


@pytest.mark.parametrize(
    "name", ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "app.min.js", "site.min.css"]
)
async def test_a_generated_file_is_marked_not_supported_and_never_parsed(
    harness: Harness, name: str
) -> None:
    events = await harness.run(name, b'{"lockfileVersion": 3}')

    assert events == DONE
    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert "generated file" in write["reason"]
    assert not harness.parsed_as_markdown()
    assert not harness.parsed_as_code()
    harness.json.parse.assert_not_awaited()
    harness.yaml.parse.assert_not_awaited()


async def test_line_delimited_json_is_marked_not_supported(harness: Harness) -> None:
    events = await harness.run("events.ndjson", b'{"a": 1}\n{"a": 2}\n')

    assert events == DONE
    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert ".ndjson" in write["reason"]
    assert not harness.parsed_as_markdown()
    harness.json.parse.assert_not_awaited()


async def test_binary_content_under_a_text_name_is_marked_not_supported(harness: Harness) -> None:
    events = await harness.run("backup.sql", b"SQLite format 3\x00" + b"\x00\x01" * 64)

    assert events == DONE
    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert "binary data" in write["reason"]
    assert not harness.parsed_as_markdown()


# -- the text fallback is capped ----------------------------------------------


async def test_an_oversized_text_file_is_marked_too_large_and_never_reaches_markdown(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 1024 * 1024)
    content = b"INSERT INTO t VALUES (1);\n" * 60_000
    size = len(content)
    assert size > engine.MAX_FILE_SIZE_BYTES

    events = await harness.run("dump.sql", content)

    assert events == DONE
    assert not harness.parsed_as_markdown()
    harness.md.extract_and_replace_images.assert_not_called()
    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert "1.5 MB" in write["reason"]
    assert "up to 1 MB" in write["reason"]
    assert "CODE_FILE_MAX_SIZE_MB" in write["reason"]
    assert write["reason"].endswith("and then choose Index all on the repository.")
    # The log names the file and its size, so an operator can find it.
    assert "dump.sql" in harness.log_lines()
    assert str(size) in harness.log_lines()


async def test_oversized_source_is_marked_too_large_without_being_parsed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 1024)

    events = await harness.run("big.py", b"x = 1\n" * 1024)

    assert events == DONE
    assert not harness.parsed_as_code()
    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert "CODE_FILE_MAX_SIZE_MB" in write["reason"]


async def test_a_small_text_file_with_no_grammar_still_falls_back_to_text_parsing(
    harness: Harness,
) -> None:
    events = await harness.run("deploy.sh", b"#!/bin/sh\necho deploying\n")

    assert events == DONE
    harness.md.parse_to_blocks.assert_awaited_once()
    assert harness.md.parse_to_blocks.await_args.args[0] == "#!/bin/sh\necho deploying"
    assert harness.status_writes() == []
    assert len(_Pipeline.applied) == 1


async def test_source_with_a_grammar_still_goes_to_the_code_parser(harness: Harness) -> None:
    events = await harness.run("main.py", b"def main():\n    return 1\n")

    assert events == DONE
    assert harness.parsed_as_code()
    assert not harness.parsed_as_markdown()
    assert len(_Pipeline.applied) == 1


# -- a skipped file is finished, not stuck ------------------------------------


async def test_a_skipped_file_leaves_no_parse_marked_in_progress(harness: Harness) -> None:
    """The parsing-service path marks parsingStatus IN_PROGRESS before it hands
    the file over. A skip that left it there, with processingStartedAt cleared,
    read as a crashed parse, and stale recovery republished the record for ever."""
    harness.graph.get_document.return_value["parsingStatus"] = ProgressStatus.IN_PROGRESS.value

    await harness.run("package-lock.json", b"{}")

    (write,) = harness.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert write["parsingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert write["processingStartedAt"] is None


async def test_a_skip_does_not_overwrite_a_parse_that_already_finished(harness: Harness) -> None:
    harness.graph.get_document.return_value["parsingStatus"] = ProgressStatus.COMPLETED.value

    await harness.run("package-lock.json", b"{}")

    (write,) = harness.status_writes()
    assert "parsingStatus" not in write


# -- uploads are not filtered -------------------------------------------------


@pytest.fixture
def uploaded(harness: Harness) -> Harness:
    """The same code path, reached by a source file someone uploaded."""
    harness.graph.get_document.return_value["recordType"] = "FILE"
    return harness


async def test_an_uploaded_minified_script_is_still_parsed_as_source(uploaded: Harness) -> None:
    events = await uploaded.run("app.min.js", b"function a(){return 1}")

    assert events == DONE
    assert uploaded.parsed_as_code()
    assert uploaded.status_writes() == []


async def test_an_uploaded_shell_script_over_the_code_limit_is_still_read_as_text(
    uploaded: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 1024)

    events = await uploaded.run("deploy.sh", b"echo deploying\n" * 1024)

    assert events == DONE
    uploaded.md.parse_to_blocks.assert_awaited_once()
    assert uploaded.status_writes() == []


async def test_uploaded_source_over_the_code_limit_is_refused_as_before(
    uploaded: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 1024)

    await uploaded.run("big.py", b"x = 1\n" * 1024)

    assert not uploaded.parsed_as_code()
    (write,) = uploaded.status_writes()
    assert write["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert write["reason"].endswith("and then upload it again.")


# -- the event loop keeps turning ---------------------------------------------


def _large_python_source(target_bytes: int) -> bytes:
    parts: list[str] = []
    size = 0
    index = 0
    while size < target_bytes:
        part = (
            f"class Service{index}:\n"
            f"    def handle_{index}(self, value: int) -> int:\n"
            f"        total = 0\n"
            f"        for k in range(value):\n"
            f"            total += k * {index}\n"
            f"        return total\n\n\n"
        )
        parts.append(part)
        size += len(part)
        index += 1
    return "".join(parts).encode()


async def test_the_event_loop_keeps_ticking_while_a_large_source_file_is_parsed(
    harness: Harness,
) -> None:
    """The tree-sitter parse used to run on the loop itself, so nothing else on
    it ran until the parse returned: this heartbeat ticked zero times.

    Counting ticks rather than timing them keeps this stable on a busy runner:
    a blocked loop gives exactly zero, a free one gives dozens.
    """
    harness.processor.parsers[ExtensionTypes.CODE.value] = CodeFileParser()
    source = _large_python_source(1024 * 1024)
    ticks = 0
    running = True

    async def heartbeat() -> None:
        nonlocal ticks
        while running:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started = time.perf_counter()
    try:
        ticks = 0
        events = await harness.run("services.py", source)
        ticks_during_parse = ticks
    finally:
        running = False
        await beat
    elapsed = time.perf_counter() - started

    assert events == DONE
    assert _Pipeline.applied[0].record.block_containers.blocks
    assert ticks_during_parse >= 3, (
        f"the event loop ticked {ticks_during_parse} time(s) in the {elapsed:.2f}s the parse took"
    )
