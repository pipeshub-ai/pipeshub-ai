"""A .log/.cfg costs a bounded amount to index, whatever its size and whatever reads it: at most
TEXT_LOG_MAX_BYTES of decoded text reaches the index, a file over TEXT_LOG_HARD_LIMIT_BYTES is not
downloaded, and no LLM classifies it."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.config import file_types
from app.config.file_types import TEXT_LOG_HARD_LIMIT_BYTES, TEXT_LOG_MAX_BYTES
from app.modules.parsers.text.capped_text import CappedText, prepare_capped_text
from app.modules.transformers.document_extraction import _NOT_CODE
from app.utils.user_errors import file_too_large_to_read, partly_indexed_text

from .new_type_harness import handler_run, legacy
from .samples import all_text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.timeout(300)
KIB = 1024


def test_the_default_limits() -> None:
    assert TEXT_LOG_MAX_BYTES == 256 * KIB
    assert TEXT_LOG_HARD_LIMIT_BYTES == 50 * 1024 * 1024
    assert file_too_large_to_read("log") == (
        "This .log file is too large to index (over 50 MB). It can still be found by its name."
    )
    assert partly_indexed_text() == (
        "Only the first and last part of this file is indexed (file is larger than 256 KiB)."
    )


def _import_with(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = "from app.config import file_types as f; print(f.TEXT_LOG_MAX_BYTES, f.TEXT_LOG_HARD_LIMIT_BYTES)"
    return subprocess.run([sys.executable, "-c", code], env={**os.environ, **env}, capture_output=True,
                          text=True, cwd=os.getcwd(), timeout=120)


def test_the_limits_can_be_set_and_the_cap_may_not_exceed_the_limit() -> None:
    ok = _import_with({"TEXT_LOG_MAX_BYTES": "1024", "TEXT_LOG_HARD_LIMIT_BYTES": "2000"})
    assert ok.stdout.strip().endswith("1024 2000"), ok.stderr
    bad = _import_with({"TEXT_LOG_MAX_BYTES": "3000", "TEXT_LOG_HARD_LIMIT_BYTES": "2000"})
    assert bad.returncode != 0 and "must not exceed" in bad.stderr


def test_a_cap_too_small_for_the_marker_is_refused_when_it_is_set() -> None:
    from app.modules.parsers.text.capped_text import minimum_cap

    assert minimum_cap() <= file_types.TEXT_LOG_MIN_BYTES
    bad = _import_with({"TEXT_LOG_MAX_BYTES": "64"})
    assert bad.returncode != 0 and "must be at least" in bad.stderr


def _log(size: int) -> bytes:
    line = b"2026-11-14T09:00:00Z INFO worker handled a request with id 0123456789\n"
    body = (line * (size // len(line) + 1))[:size]
    return body[: body.rfind(b"\n") + 1] if size > len(line) else body


@pytest.mark.parametrize(
    ("size", "truncated"),
    [(KIB, False), (256 * KIB - 1, False), (256 * KIB, False), (256 * KIB + 100, True), (5 * 1024 * KIB, True)],
    ids=["1KiB", "cap-1", "cap", "cap+1", "5MiB"],
)
def test_each_size_is_whole_up_to_the_cap_and_its_ends_beyond_it(size: int, truncated: bool) -> None:
    data = _log(size)
    result = prepare_capped_text(data)
    assert isinstance(result, CappedText)
    assert result.truncated is truncated
    assert len(result.text.encode("utf-8")) <= TEXT_LOG_MAX_BYTES
    assert result.text.rstrip("\n").endswith(data.decode().rstrip("\n").rsplit("\n", 1)[-1])
    if truncated:
        assert "bytes omitted" in result.text
        for line in result.text.splitlines():
            assert line.startswith(("2026-11-14T09:00:00Z INFO", "[... "))


@pytest.mark.parametrize("codec", ["utf-16-le", "shift_jis"])
def test_a_multibyte_log_is_capped_in_decoded_bytes(codec: str) -> None:
    line = "設定ファイル: サーバーのホスト名とポート番号を記述します。\n"
    text = "最初の行です。\n" + line * 20_000 + "最後の行です。\n"
    result = prepare_capped_text(text.encode(codec))
    assert isinstance(result, CappedText) and result.truncated
    assert len(result.text.encode("utf-8")) <= TEXT_LOG_MAX_BYTES
    lines = result.text.splitlines()
    assert lines[0] == "最初の行です。"
    assert lines[-1] == "最後の行です。"
    assert all(f"{x}\n" in (line, "最初の行です。\n", "最後の行です。\n") or x.startswith("[... ") for x in lines)


@pytest.mark.parametrize(
    ("mime", "reader"),
    [
        ("application/octet-stream", "process_plain_text_document"),  # a new file: the log reader
        ("text/plain", "process_txt_document"),  # stored before: main's text reader, unchanged
    ],
)
async def test_both_readers_get_at_most_the_cap_and_the_record_says_so(mime: str, reader: str) -> None:
    data = _log(5 * 1024 * KIB)
    containers, _, record = await legacy("log", mime, data)
    text = "\n".join(all_text(c) for c in containers)
    assert len(text.encode("utf-8")) <= TEXT_LOG_MAX_BYTES
    assert "bytes omitted" in text
    # The record schema accepts `indexingNote`; a later release writes it (expand, then write).
    assert "indexingNote" not in record


async def test_a_log_read_as_code_is_capped_too() -> None:
    from app.events.events import DedupDecision
    from tests.unit.events.test_events import (
        _drain,
        _make_event_payload,
        _make_event_processor,
    )

    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    seen: dict[str, bytes] = {}

    async def code(**kw: object) -> AsyncIterator[object]:
        seen["bytes"] = kw["code_binary"]  # type: ignore[assignment]
        return
        yield

    processor.process_code_document = code
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        await _drain(ep.on_event(_make_event_payload(
            extension="log", mime_type="application/javascript", buffer=_log(2 * 1024 * KIB),
            record_name="build.log",
        )))
    assert len(seen["bytes"]) <= TEXT_LOG_MAX_BYTES


@pytest.mark.parametrize("mime", ["text/plain", "application/octet-stream"])
async def test_the_parsing_service_gets_at_most_the_cap(mime: str) -> None:
    from app.events.events import DedupDecision, EventProcessor
    from tests.unit.events.test_events import (
        _drain,
        _make_event_payload,
        _make_event_processor,
    )

    ep, _, _, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}
    ep.parsing_client = SimpleNamespace(circuit_open=False)
    seen: dict[str, object] = {}

    async def orchestrate(**kw: object) -> AsyncIterator[object]:
        seen.update(kw)
        return
        yield

    with patch.object(EventProcessor, "_use_service_pipeline", return_value=True), \
         patch.object(ep, "_orchestrate_via_services", side_effect=orchestrate), \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        await _drain(ep.on_event(_make_event_payload(extension="log", mime_type=mime, buffer=_log(3 * 1024 * KIB))))
    assert "file_content" in seen, "the parsing service was not called"
    assert len(seen["file_content"]) <= TEXT_LOG_MAX_BYTES  # type: ignore[arg-type]


@pytest.mark.parametrize(("size", "refused"), [(TEXT_LOG_HARD_LIMIT_BYTES - 1, False),
                                               (TEXT_LOG_HARD_LIMIT_BYTES, False),
                                               (TEXT_LOG_HARD_LIMIT_BYTES + 1, True)])
@pytest.mark.parametrize("mime", ["application/octet-stream", "text/plain", "application/javascript"])
def test_the_hard_limit_is_by_declared_size_on_every_reader(size: int, refused: bool, mime: str) -> None:
    decision = file_types.gate_decision(
        mime=mime, extension="log", record_name="a.log", is_code_file=False, code_file_extension=None,
        size_bytes=size,
    )
    assert (decision.refusal == "too_large") is refused


@pytest.mark.parametrize("mime", ["application/octet-stream", "text/plain", "application/javascript"])
async def test_an_unsized_log_streamed_past_the_limit_is_not_supported(mime: str) -> None:
    seen: dict[str, object] = {}
    record, downloaded = await handler_run("log", mime, b"", too_large=True, download_kwargs=seen)
    assert downloaded and seen["max_bytes"] == TEXT_LOG_HARD_LIMIT_BYTES
    assert record["indexingStatus"] == "FILE_TYPE_NOT_SUPPORTED"
    assert record["reason"] == file_too_large_to_read("log")


async def test_a_refused_log_is_not_fetched_again_until_it_changes() -> None:
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from tests.unit.connectors.core.test_data_source_entities_processor import (
        _make_record,
    )

    stored = _make_record(record_name="big.log", mime_type="text/x-log", external_revision_id="r1")
    stored.indexing_status = "FILE_TYPE_NOT_SUPPORTED"
    again = _make_record(record_name="big.log", mime_type="text/x-log", external_revision_id="r1")
    changed = _make_record(record_name="big.log", mime_type="text/x-log", external_revision_id="r2")
    assert DataSourceEntitiesProcessor._keeps_stored_indexing(again, stored, reindex_restored=False)
    assert not DataSourceEntitiesProcessor._keeps_stored_indexing(changed, stored, reindex_restored=False)


async def test_a_40_mb_log_sends_the_embedder_a_bounded_amount_of_text() -> None:
    from tests.unit.modules.transformers.test_vectorstore_embedding_text import (
        _capturing_vectorstore,
    )

    containers, _, _ = await legacy("log", "application/octet-stream", _log(40 * 1024 * 1024))
    vs, capture = _capturing_vectorstore()
    await vs.index_documents(block_containers=containers[0], org_id="org-1", record_id="r1", virtual_record_id="v1")
    embedded = sum(len(t.encode("utf-8")) for t in capture.dense)
    # Each block and its sentence points embed the same text at most a few times over.
    assert 0 < embedded <= 4 * TEXT_LOG_MAX_BYTES
    assert len(capture.dense) <= 4 * TEXT_LOG_MAX_BYTES // 40


@pytest.mark.parametrize(("name", "classified"), [("server.log", False), ("app.CFG", False),
                                                  ("report.pdf", True), ("notes.txt", True)])
async def test_no_llm_classifies_a_log_or_cfg(name: str, classified: bool) -> None:
    from app.models.blocks import BlocksContainer
    from app.modules.pipeline.stages.classify_io import GraphClassifyIO

    classifier = AsyncMock(return_value=None)
    io = GraphClassifyIO.__new__(GraphClassifyIO)
    io._classifier = classifier
    io._job = SimpleNamespace(record_ids=("r1",))
    io._record_header = AsyncMock(return_value=(name, "FILE"))  # type: ignore[method-assign]
    await io.classify(BlocksContainer(), "org-1", [])
    assert classifier.await_count == (1 if classified else 0)


@pytest.mark.parametrize(("name", "classified"), [("server.log", False), ("report.pdf", True)])
async def test_the_in_process_extraction_skips_a_log_or_cfg(name: str, classified: bool) -> None:
    from app.modules.transformers.document_extraction import DocumentExtraction

    extraction = DocumentExtraction.__new__(DocumentExtraction)
    extraction.logger = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None,
                                        error=lambda *a, **k: None)
    extraction._classify_code = AsyncMock(return_value=_NOT_CODE)  # type: ignore[method-assign]
    extraction.extract_metadata = AsyncMock(return_value=None)  # type: ignore[method-assign]
    record = SimpleNamespace(record_name=name, block_containers=SimpleNamespace(blocks=[], block_groups=[]),
                             org_id="org-1", record_type=SimpleNamespace(value="FILE"), id="r1",
                             semantic_metadata="unset", origin=None)
    with patch("app.modules.transformers.document_extraction.record_is_push_origin", return_value=False):
        await extraction.apply(SimpleNamespace(record=record))
    assert extraction.extract_metadata.await_count == (1 if classified else 0)
    assert record.semantic_metadata is None


@pytest.mark.parametrize(("name", "record_type", "classified"), [
    ("server.log", "FILE", False), ("app.CFG", "FILE", False), ("build.log", "CODE_FILE", False),
    ("report.pdf", "FILE", True),
])
async def test_the_classify_endpoint_makes_no_model_call_for_a_log_or_cfg(
    name: str, record_type: str, classified: bool,
) -> None:
    from app.modules.transformers.document_extraction import DocumentExtraction

    extraction = DocumentExtraction.__new__(DocumentExtraction)
    extraction.logger = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None)
    extraction._classify_code = AsyncMock(return_value=_NOT_CODE)  # type: ignore[method-assign]
    extraction._classify_blocks = AsyncMock(return_value=None)  # type: ignore[method-assign]
    # What the standalone /classify route calls.
    assert await extraction.classify([], "org-1", ["Engineering"], record_name=name, record_type=record_type) is None
    calls = extraction._classify_code.await_count + extraction._classify_blocks.await_count
    assert (calls > 0) is classified
