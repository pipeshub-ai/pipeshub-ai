"""Which records ``EventProcessor`` hands to the code-file path, in both parsing modes.

In-process, ``Processor.process_code_document`` decides how a repository file is
read. With ``USE_PARSING_SERVICE=true`` the parsing service picks a parser from
mime and extension alone, so the same decision has to be made before it is called.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.events import EventProcessor
from app.modules.parsers.code_parser import engine
from tests.unit.events.test_orchestrator_flow import (
    _make_event_data,
    _make_event_processor,
    _noop_gen,
    _service_clients,
)

if TYPE_CHECKING:
    from app.services.messaging.config import IndexingEvent


def _repository_event(name: str, mime_type: str, content: bytes) -> dict[str, Any]:
    event = _make_event_data()
    extension = name.rsplit(".", 1)[-1] if "." in name else None
    event["payload"].update(
        recordName=name,
        extension=extension,
        mimeType=mime_type,
        buffer=content,
        filePath=f"src/{name}",
        connectorName="GITLAB",
    )
    return event


def _legacy_processor() -> MagicMock:
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    for method in (
        "process_code_document", "process_txt_document", "process_structured_document",
        "process_delimited_document", "process_md_document",
    ):
        setattr(processor, method, MagicMock(side_effect=lambda **_: _noop_gen()))
    return processor


def _as_code_file(ep: EventProcessor, mime_type: str) -> None:
    record = ep.graph_provider.get_document.return_value
    record["recordType"] = "CODE_FILE"
    record["mimeType"] = mime_type


async def _drain(ep: EventProcessor, event: dict[str, Any]) -> list[IndexingEvent]:
    return [e.event async for e in ep.on_event(event)]


@pytest.mark.parametrize(
    ("record_type", "mime_type", "extension", "name", "expected"),
    [
        ("FILE", "text/x-python", "py", "main.py", True),
        ("CODE_FILE", "text/plain", "py", "main.py", True),
        ("CODE_FILE", "text/plain", "css", "site.css", True),
        ("CODE_FILE", "text/plain", "csv", "export.csv", True),
        ("CODE_FILE", "application/json", "json", "package-lock.json", True),
        ("CODE_FILE", "application/yaml", "yaml", "pnpm-lock.yaml", True),
        # Formats with a parser of their own keep it.
        ("CODE_FILE", "application/json", "json", "fixtures.json", False),
        ("CODE_FILE", "text/csv", "csv", "export.csv", False),
        ("CODE_FILE", "text/markdown", "md", "README.md", False),
        # Uploads are untouched: only repository files are capped and filtered.
        ("FILE", "text/plain", "txt", "notes.txt", False),
        ("FILE", "application/json", "json", "package-lock.json", False),
    ],
)
def test_which_records_the_code_file_path_decides(
    record_type: str, mime_type: str, extension: str, name: str, expected: bool
) -> None:
    assert EventProcessor._reads_as_code_file(record_type, mime_type, extension, name) is expected


# -- in-process parsing --------------------------------------------------------


async def test_a_plain_text_repository_file_goes_to_the_code_file_path() -> None:
    processor = _legacy_processor()
    ep = _make_event_processor(processor=processor)
    _as_code_file(ep, "text/plain")

    await _drain(ep, _repository_event("site.css", "text/plain", b"body { margin: 0 }"))

    processor.process_code_document.assert_called_once()
    assert processor.process_code_document.call_args.kwargs["recordName"] == "site.css"
    processor.process_txt_document.assert_not_called()


async def test_a_lock_file_goes_to_the_code_file_path_not_the_json_parser() -> None:
    processor = _legacy_processor()
    ep = _make_event_processor(processor=processor)
    _as_code_file(ep, "application/json")

    await _drain(ep, _repository_event("package-lock.json", "application/json", b"{}"))

    processor.process_code_document.assert_called_once()
    processor.process_structured_document.assert_not_called()


async def test_an_uploaded_text_file_is_still_read_as_text() -> None:
    processor = _legacy_processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_document.return_value["mimeType"] = "text/plain"
    event = _repository_event("notes.txt", "text/plain", b"meeting notes")
    event["payload"]["connectorName"] = ""

    await _drain(ep, event)

    processor.process_txt_document.assert_called_once()
    processor.process_code_document.assert_not_called()


# -- through the parsing service -----------------------------------------------


def _service_processor(processor: MagicMock) -> tuple[EventProcessor, MagicMock]:
    parsing_client, extraction_client, sink_orchestrator = _service_clients()
    ep = _make_event_processor(
        parsing_client=parsing_client,
        extraction_client=extraction_client,
        sink_orchestrator=sink_orchestrator,
        processor=processor,
    )
    return ep, parsing_client


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_generated_file_is_never_sent_to_the_parsing_service() -> None:
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "application/json")

    await _drain(ep, _repository_event("package-lock.json", "application/json", b"{}"))

    parsing_client.parse.assert_not_awaited()
    # The code-file path writes the status and the reason people see.
    processor.process_code_document.assert_called_once()


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_an_oversized_text_file_is_never_sent_to_the_parsing_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "MAX_FILE_SIZE_BYTES", 1024)
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "text/plain")

    await _drain(ep, _repository_event("dump.sql", "text/plain", b"INSERT INTO t VALUES (1);\n" * 100))

    parsing_client.parse.assert_not_awaited()
    processor.process_code_document.assert_called_once()


@pytest.mark.parametrize(
    ("name", "extension", "mime_type"),
    [
        ("export.csv", "csv", "text/csv"),
        ("export.tsv", "tsv", "text/tab-separated-values"),
        ("fixtures.json", "json", "application/json"),
        ("values.yml", "yaml", "application/yaml"),
    ],
)
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_data_file_stored_as_plain_text_is_sent_as_its_own_format(
    name: str, extension: str, mime_type: str
) -> None:
    """The parsing service lets mime win, and text/plain would mean Markdown."""
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "text/plain")

    await _drain(ep, _repository_event(name, "text/plain", b"a,b\n1,2\n"))

    sent = parsing_client.parse.await_args.kwargs
    assert (sent["extension"], sent["mime_type"]) == (extension, mime_type)
    processor.process_code_document.assert_not_called()


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_source_is_sent_with_the_extension_from_its_name_when_the_event_has_none() -> None:
    """With text/plain and no extension the parsing service would read it as Markdown."""
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "text/plain")
    event = _repository_event("main.py", "text/plain", b"x = 1\n")
    event["payload"]["extension"] = "unknown"

    await _drain(ep, event)

    assert parsing_client.parse.await_args.kwargs["extension"] == "py"


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_source_is_sent_with_its_names_extension_when_the_declared_one_disagrees() -> None:
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "text/plain")
    event = _repository_event("main.py", "text/plain", b"x = 1\n")
    event["payload"]["extension"] = "json"

    await _drain(ep, event)

    assert parsing_client.parse.await_args.kwargs["extension"] == "py"


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_an_uploaded_minified_script_is_still_sent_to_the_parsing_service() -> None:
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    ep.graph_provider.get_document.return_value["mimeType"] = "text/javascript"
    event = _repository_event("app.min.js", "text/javascript", b"function a(){return 1}")
    event["payload"]["connectorName"] = ""

    await _drain(ep, event)

    parsing_client.parse.assert_awaited_once()
    processor.process_code_document.assert_not_called()


@pytest.mark.parametrize("name", ["main.py", "site.css"])
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_source_and_small_text_files_are_sent_unchanged(name: str) -> None:
    processor = _legacy_processor()
    ep, parsing_client = _service_processor(processor)
    _as_code_file(ep, "text/plain")

    await _drain(ep, _repository_event(name, "text/plain", b"x = 1\n"))

    sent = parsing_client.parse.await_args.kwargs
    assert (sent["extension"], sent["mime_type"]) == (name.rsplit(".", 1)[-1], "text/plain")
    processor.process_code_document.assert_not_called()
