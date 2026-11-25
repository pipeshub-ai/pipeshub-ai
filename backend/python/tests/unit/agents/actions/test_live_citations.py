"""Live connector content is citable (P1b C-19).

A tool that reads a file straight from a connector renders it with reference
numbers from the run's `CitationRefMapper` and registers its temporary record
in `tool_records`, as a fetched indexed record is. The answer's citations of
it then resolve, marked live (C-18), with no record-page link.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.actions.util.live_record import LiveCitations, live_file_record
from app.agents.agent_loop.respond import _log_live_citations
from app.config.constants.arangodb import Connectors
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.utils.chat_helpers import CITE_BLOCK_REFS_HINT, CitationRefMapper
from app.utils.citations import (
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)

if TYPE_CHECKING:
    from app.models.entities import FileRecord

NORMALIZERS = [normalize_citations_and_chunks, normalize_citations_and_chunks_for_agent]
SECRET = "The launch moves to May."


def _record(*, indexed: bool = True) -> FileRecord:
    record = live_file_record(
        {"org_id": "org-1", "tool_to_toolset_map": {"drive.get_file_content": "drive-toolset"}},
        tool="drive.get_file_content",
        connector=Connectors.GOOGLE_DRIVE,
        external_record_id="file-q3",
        record_name="Q3 plan.txt",
        mime_type="text/plain",
        extension="txt",
    )
    texts = [SECRET, "The budget is unchanged."]
    blocks = [
        Block(**({"index": i} if indexed else {}), type=BlockType.TEXT, format=DataFormat.TXT, data=text)
        for i, text in enumerate(texts)
    ]
    record.block_containers = BlocksContainer(blocks=blocks, block_groups=[])
    return record


def _normalize(normalize: Any, answer: str, state: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:  # noqa: ANN401
    records = state.get("tool_records") or []
    ref_to_url = state["citation_ref_mapper"].ref_to_url
    if normalize is normalize_citations_and_chunks:
        return normalize(answer, [], records, ref_to_url=ref_to_url)
    return normalize(answer, [], {}, records, ref_to_url=ref_to_url)


def test_the_content_cites_blocks_with_refs_from_the_run_mapper() -> None:
    mapper = CitationRefMapper()
    _ = mapper.get_or_create_ref("https://app.test/record/rec-1/preview#blockIndex=0")
    state: dict[str, Any] = {"citation_ref_mapper": mapper}
    record = _record()
    text = "".join(item.text for item in LiveCitations(state).render(record))
    # Numbered after what the run already cites, in the run's own mapper.
    assert f"[ref2] {SECRET}" in text
    assert "[ref3] The budget is unchanged." in text
    assert state["citation_ref_mapper"] is mapper
    assert mapper.ref_to_url["ref2"] == f"/record/{record.id}/preview#blockIndex=0"
    assert text.endswith(CITE_BLOCK_REFS_HINT)
    assert "External ID: file-q3" in text


def test_a_run_without_a_mapper_gets_one() -> None:
    state: dict[str, Any] = {"citation_ref_mapper": None}
    _ = LiveCitations(state).render(_record())
    assert list(state["citation_ref_mapper"].ref_to_url) == ["ref1", "ref2"]


def test_blocks_without_an_index_are_addressed_by_position() -> None:
    state: dict[str, Any] = {}
    record = _record(indexed=False)
    citations = LiveCitations(state)
    _ = citations.render(record)
    citations.register(record)
    _, [second] = _normalize(normalize_citations_and_chunks, "Budget [source](ref2).", state)
    assert second["content"] == "The budget is unchanged."


def test_registered_once_in_tool_records_without_a_build_time() -> None:
    state: dict[str, Any] = {"tool_records": [{"id": "rec-indexed"}]}
    record = _record()
    citations = LiveCitations(state)
    _ = citations.render(record)
    citations.register(record)
    citations.register(record)
    assert [r["id"] for r in state["tool_records"]] == ["rec-indexed", record.id]
    registered = state["tool_records"][1]
    assert "created_at" not in registered and "updated_at" not in registered
    assert registered["external_record_id"] == "file-q3"


@pytest.mark.parametrize("normalize", NORMALIZERS, ids=["chat", "agent"])
def test_a_cited_live_block_is_a_live_citation_without_a_record_page(normalize: Any) -> None:  # noqa: ANN401
    state: dict[str, Any] = {}
    record = _record()
    citations = LiveCitations(state)
    _ = citations.render(record)
    citations.register(record)
    answer, [citation] = _normalize(normalize, "It moves to May [source](ref1).", state)
    meta = citation["metadata"]
    assert citation["content"] == SECRET
    assert (meta["sourceKind"], meta["externalRecordId"], meta["connector"], meta["connectorId"]) == (
        "live", "file-q3", Connectors.GOOGLE_DRIVE.value, "drive-toolset",
    )
    assert meta["recordId"] == record.id
    assert meta["previewRenderable"] is False
    assert "/record/" not in (meta["webUrl"] or "")
    assert meta["updatedAt"] is None
    assert answer.startswith("It moves to May [1](")


@pytest.mark.parametrize("normalize", NORMALIZERS, ids=["chat", "agent"])
def test_an_unknown_live_ref_is_still_dropped(normalize: Any) -> None:  # noqa: ANN401
    state: dict[str, Any] = {}
    record = _record()
    citations = LiveCitations(state)
    _ = citations.render(record)
    citations.register(record)
    unknown = "/record/live:0000000000000000000000000000dead/preview#blockIndex=0"
    answer, cited = _normalize(normalize, f"May [source](ref9) and [source]({unknown}).", state)
    assert cited == []
    assert "ref9" not in answer


@pytest.mark.parametrize("normalize", NORMALIZERS, ids=["chat", "agent"])
def test_content_rendered_but_never_registered_is_not_cited(normalize: Any) -> None:  # noqa: ANN401
    """A file over the token limit is rendered, then withheld from the model."""
    state: dict[str, Any] = {}
    _ = LiveCitations(state).render(_record())
    _, cited = _normalize(normalize, "May [source](ref1).", state)
    assert cited == []


def test_logs_count_live_citations_and_never_their_content(caplog: pytest.LogCaptureFixture) -> None:
    state: dict[str, Any] = {}
    record = _record()
    citations = LiveCitations(state)
    _ = citations.render(record)
    # Other suites reconfigure shared loggers; these two are this test's own, at INFO.
    finalizer_log = logging.getLogger("tests.c19.finalizer")
    with caplog.at_level(logging.INFO, logger="app.agents.actions.util.live_record"), \
            caplog.at_level(logging.INFO, logger=finalizer_log.name):
        citations.register(record)
        _, cited = _normalize(normalize_citations_and_chunks, "May [source](ref1).", state)
        _log_live_citations(finalizer_log, [*cited, {"metadata": {"recordId": "rec-1"}}])
    messages = [r.getMessage() for r in caplog.records]
    assert "citations.live_registered connector=DRIVE blocks=2 toolRecords=1" in messages
    assert "citations.live_resolved live=1 citations=2" in messages
    body = "\n".join(messages)
    for leak in (SECRET, "file-q3", record.id, "Q3 plan"):
        assert leak not in body
