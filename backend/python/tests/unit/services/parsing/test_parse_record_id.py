"""The optional record id travels from the indexing caller to the PDF layout parser, and its absence is accepted."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import BlocksContainer
from app.services.parsing.client import ParsingClient
from app.services.parsing.providers.pdfplumber_parser import PdfPlumberParser
from tests.unit.services.parsing.test_parse_route_file_path import _client_and_parser
from tests.unit.services.parsing.test_parsing_client import (
    _make_response,
    _success_response,
)


@pytest.mark.parametrize(("form", "expected"), [({"record_id": "rec-42"}, "rec-42"), ({}, None)], ids=["given", "absent"])
def test_the_route_accepts_a_request_with_or_without_a_record_id(form, expected) -> None:
    client, parser = _client_and_parser()
    with patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/parse",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"record_name": "a.pdf", "extension": "pdf", "provider": "default", **form},
        )
    assert response.status_code == 200
    assert parser.parse.await_args.args[2].get("record_id") == expected


def test_an_old_caller_that_sends_no_record_id_field_at_all_is_unchanged() -> None:
    client, parser = _client_and_parser()
    with patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/parse",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"record_name": "a.pdf", "extension": "pdf", "provider": "default"},
        )
    assert response.status_code == 200 and "record_id" not in parser.parse.await_args.args[2]


@pytest.mark.parametrize(("record_id", "sent"), [("rec-7", True), (None, False)])
async def test_the_client_sends_the_field_only_when_it_has_a_record_id(record_id, sent) -> None:
    client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=1)
    post = AsyncMock(return_value=_make_response(200, _success_response()))
    with patch.object(client, "_post_multipart", new=post):
        await client.parse(file_content=b"data", record_name="a.csv", mime_type="text/csv", extension="csv", record_id=record_id)
    data = post.await_args.kwargs["data"]
    assert ("record_id" in data) is sent and (not sent or data["record_id"] == "rec-7")


async def test_the_provider_hands_the_record_id_and_the_table_model_flag_to_the_layout_parser() -> None:
    processor = MagicMock()
    processor.parse_document = AsyncMock(return_value=[])
    processor.create_blocks = AsyncMock(return_value=BlocksContainer(blocks=[], block_groups=[]))
    await PdfPlumberParser(processor).parse(b"%PDF", "a.pdf", {"record_id": "rec-9"})
    assert processor.parse_document.await_args.kwargs == {"table_model": True, "record_id": "rec-9"}
    await PdfPlumberParser(processor).parse(b"%PDF", "a.pdf", None)
    assert processor.parse_document.await_args.kwargs == {"table_model": True, "record_id": None}


async def test_the_in_process_pdf_path_passes_the_record_id() -> None:
    from tests.unit.events.test_processor import (
        _base_record_dict,
        _collect,
        _make_processor,
    )

    proc, _, gp, _ = _make_processor()
    gp.get_document.return_value = _base_record_dict(mimeType="application/pdf")
    with patch("app.events.processor.PDFPlumberOpenCVProcessor") as cls, patch("app.events.processor.IndexingPipeline") as pipeline:
        inst = AsyncMock()
        inst.parse_document.return_value = []
        inst.create_blocks.return_value = []
        cls.return_value = inst
        pipeline.return_value = AsyncMock()
        await _collect(proc.process_pdf_with_pdf_plumber(recordName="t.pdf", recordId="rec-1", pdf_binary=b"pdf", virtual_record_id="vr-1"))
    assert inst.parse_document.call_args.kwargs == {"table_model": True, "record_id": "rec-1"}


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_the_service_pipeline_sends_the_record_id_to_the_parsing_service() -> None:
    from tests.unit.events.test_orchestrator_flow import (
        _indexing_sink,
        _make_event_data,
        _make_event_processor,
        _service_parsing_client,
    )

    parsing_client, sink = _service_parsing_client(), _indexing_sink()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink)
    _ = [event async for event in ep.on_event(_make_event_data())]
    assert parsing_client.parse.await_args.kwargs["record_id"] == "rec-1"


def test_the_route_cuts_a_long_record_id() -> None:
    client, parser = _client_and_parser()
    with patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/parse",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            data={"record_name": "a.pdf", "extension": "pdf", "provider": "default", "record_id": "r" * 300},
        )
    assert response.status_code == 200 and parser.parse.await_args.args[2]["record_id"] == "r" * 128
