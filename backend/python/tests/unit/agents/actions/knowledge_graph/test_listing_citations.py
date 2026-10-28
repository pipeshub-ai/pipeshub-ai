"""A record a listing showed can be cited for what the listing said about it."""

from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.listing_citations import (
    cite_listed_records,
    context_facts,
    row_facts,
)
from app.agents.actions.knowledge_graph.models import (
    LookupMatch,
    LookupResult,
    NavigationView,
    NodeRow,
)
from app.agents.actions.knowledge_graph.views import (
    render_lookup_result_with_ids,
    render_navigation_view_with_ids,
)
from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import RECORD_PAGE_LISTED_FACTS, normalize_citations_and_chunks

FRONTEND = "https://pipeshub.example"
OCT_26 = 1_793_000_000_000
OCT_27 = OCT_26 + 86_400_000


def _state(**extra: object) -> dict:
    config = AsyncMock()
    config.get_config.return_value = {"frontend": {"publicEndpoint": FRONTEND}}
    return {"config_service": config, **extra}


def _graph(*docs: dict) -> AsyncMock:
    graph = AsyncMock()
    graph.get_records_by_record_ids.return_value = list(docs)
    return graph


def _doc(record_id: str, name: str) -> dict:
    return {
        "_key": record_id, "recordName": name, "recordType": "CONFLUENCE_PAGE",
        "connectorName": "CONFLUENCE", "webUrl": f"https://wiki.example/{record_id}",
    }


def _row(record_id: str, name: str) -> NodeRow:
    return NodeRow(
        id=record_id, name=name, node_type="record", sub_type="CONFLUENCE_PAGE", is_record=True,
        has_children=False, detail=None, source_created_at=OCT_26, source_modified_at=OCT_27,
    )


@pytest.mark.asyncio
async def test_a_listed_record_answers_with_a_citation_that_quotes_the_listing() -> None:
    state = _state()
    refs = await cite_listed_records(
        state, _graph(_doc("r1", "Bug Bash: Release Checklist")), "org", {"r1": "Created 2026-10-26"},
    )

    mapper = state["citation_ref_mapper"]
    answer = f"The last bug bash checklist was created on Oct 26 [source]({refs['r1']})."
    text, citations = normalize_citations_and_chunks(
        answer, [], state["tool_records"], ref_to_url=mapper.ref_to_url,
    )

    assert "[1]" in text
    assert len(citations) == 1
    meta = citations[0]["metadata"]
    assert citations[0]["content"] == "Created 2026-10-26"
    assert meta["blockType"] == RECORD_PAGE_LISTED_FACTS
    assert meta["recordId"] == "r1"
    assert meta["recordName"] == "Bug Bash: Release Checklist"


@pytest.mark.asyncio
async def test_only_records_the_graph_still_returns_are_citable() -> None:
    # A record deleted or trashed since the listing is not returned, so it gets no Citation ID.
    state = _state()
    refs = await cite_listed_records(state, _graph(_doc("r1", "Kept")), "org", {"r1": "", "gone": ""})
    assert set(refs) == {"r1"}


@pytest.mark.asyncio
async def test_listing_a_record_again_keeps_one_copy_with_the_newest_facts() -> None:
    state = _state(tool_records=[{"id": "search-hit"}])
    graph = _graph(_doc("r1", "Page"))
    first = await cite_listed_records(state, graph, "org", {"r1": "Created 2026-10-26"})
    again = await cite_listed_records(state, graph, "org", {"r1": "Status: Done"})

    assert first == again
    listed = [r for r in state["tool_records"] if r.get("id") == "r1"]
    assert [r["_listed_facts"] for r in listed] == ["Status: Done"]
    assert {"id": "search-hit"} in state["tool_records"]


@pytest.mark.asyncio
async def test_the_listing_still_works_when_records_cannot_be_cited() -> None:
    graph = AsyncMock()
    graph.get_records_by_record_ids.side_effect = RuntimeError("graph down")
    assert await cite_listed_records(_state(), graph, "org", {"r1": ""}) == {}
    # Without a public URL there is no record page to link to.
    no_url = {"config_service": None}
    assert await cite_listed_records(no_url, _graph(_doc("r1", "Page")), "org", {"r1": ""}) == {}
    assert "tool_records" not in no_url


@pytest.mark.asyncio
async def test_citation_ids_continue_the_conversations_numbering() -> None:
    mapper = CitationRefMapper()
    mapper.get_or_create_ref("https://pipeshub.example/record/x/preview#blockIndex=0")
    state = _state(citation_ref_mapper=mapper)
    refs = await cite_listed_records(state, _graph(_doc("r1", "Page")), "org", {"r1": ""})
    assert refs == {"r1": "ref2"}
    assert state["citation_ref_mapper"] is mapper


def test_the_listing_shows_each_citable_record_its_citation_id() -> None:
    view = NavigationView(
        current=None, breadcrumbs=[], rows=[_row("r1", "Cited"), _row("r2", "Not cited")],
        related=[], pagination=None, web_url=None, indexing_status=None, connector=None,
    )
    text, _ = render_navigation_view_with_ids(view, 1, None, {"r1": "ref7"})
    cited, uncited = (line for line in text.splitlines() if line.startswith("- "))
    assert "| cite=ref7" in cited
    assert "cite=" not in uncited
    assert "e.g. [source](ref7)" in text
    plain, _ = render_navigation_view_with_ids(view, 1, None, {})
    assert "cite=" not in plain

    match = LookupMatch(
        id="r1", name="PA-1", record_type="TICKET", connector_name="JIRA", web_url=None,
        indexing_status="COMPLETED", identifier_used="PA-1",
    )
    text, _ = render_lookup_result_with_ids(
        LookupResult(matches=[match], ambiguous=False, not_found_identifiers=[]), None, {"r1": "ref3"},
    )
    assert "Citation ID: ref3" in text.splitlines()


def test_the_citation_quotes_facts_not_identifiers() -> None:
    assert row_facts(_row("r1", "Page")) == "Created 2026-10-26 · modified 2026-10-27"
    block = "\n".join([
        "Record ID: r1", "Name: PA-1", "Created At: 2026-10-26 07:05:57 UTC",
        "Web URL: https://jira.example/PA-1", "Ticket Information:",
        "* Status: Done", "* Assignee: Alice Chen",
    ])
    assert context_facts(block) == "Created At: 2026-10-26 07:05:57 UTC · Status: Done · Assignee: Alice Chen"
    assert context_facts(None) == ""


def test_a_file_listing_marks_each_citable_record_too() -> None:
    from types import SimpleNamespace

    from app.agents.actions.knowledge_graph.ops.listing import _render_flat_text

    items = [
        SimpleNamespace(nodeType="record", id="r1", name="BBS 025", subType=None, webUrl=None),
        SimpleNamespace(nodeType="record", id="r2", name="BBS 024", subType=None, webUrl=None),
    ]
    response = SimpleNamespace(success=True, items=items, error=None, total=2)
    text = _render_flat_text(response, "BBS", None, {"r1": "ref4"})
    first, second = (line for line in text.splitlines() if line.startswith("  ["))
    assert first.endswith("| cite=ref4")
    assert "cite=" not in second
    assert "e.g. [source](ref4)" in text
    assert "cite=" not in _render_flat_text(response, "BBS", None, None)


@pytest.mark.asyncio
async def test_the_listing_is_quoted_even_when_a_search_holds_the_same_record() -> None:
    searched = {
        "id": "r1", "record_name": "Bug Bash: Release Checklist", "virtual_record_id": "v1",
        "semantic_metadata": {"summary": "Checklist for the OSS release bug bash."},
    }
    summary_ref_url = f"{FRONTEND}/record/r1"
    mapper = CitationRefMapper()
    summary_ref = mapper.get_or_create_ref(summary_ref_url)
    state = _state(citation_ref_mapper=mapper, tool_records=[searched])
    refs = await cite_listed_records(state, _graph(_doc("r1", "Bug Bash: Release Checklist")), "org", {"r1": "Created 2026-10-26"})
    assert refs["r1"] != summary_ref

    answer = f"It was created on Oct 26 [source]({refs['r1']}). It covers the release [source]({summary_ref})."
    _, citations = normalize_citations_and_chunks(
        answer, [], state["tool_records"], ref_to_url=mapper.ref_to_url,
        virtual_record_id_to_result={"v1": searched},
    )
    listed, summary = citations
    assert listed["content"] == "Created 2026-10-26"
    assert listed["metadata"]["webUrl"] == summary_ref_url
    assert summary["content"] == "Checklist for the OSS release bug bash."


@pytest.mark.asyncio
async def test_a_record_listed_by_name_only_is_cited_by_its_name() -> None:
    state = _state()
    doc = {**_doc("r1", "BBS 025"), "semantic_metadata": {"summary": "Text list_files never showed."}}
    refs = await cite_listed_records(state, _graph(doc), "org", {"r1": ""})
    _, citations = normalize_citations_and_chunks(
        f"See [source]({refs['r1']}).", [], state["tool_records"],
        ref_to_url=state["citation_ref_mapper"].ref_to_url,
    )
    assert citations[0]["content"] == "BBS 025"
    assert citations[0]["metadata"]["blockType"] == RECORD_PAGE_LISTED_FACTS


def test_an_uploaded_files_dates_are_kept() -> None:
    block = "\n".join([
        "Record ID: r1", "Uploaded At: 2026-10-26 07:05 UTC (upload time, not a document date)",
        "Document Date: 2026-09-01 (stated in the document)",
        "File Last Modified: 2026-10-25 10:00 UTC (the uploaded file's timestamp, not a document date)",
    ])
    facts = context_facts(block)
    assert facts.startswith("Uploaded At:")
    assert "Document Date: 2026-09-01" in facts
    assert "File Last Modified:" in facts
    assert "Record ID" not in facts


@pytest.mark.asyncio
async def test_a_listing_that_states_nothing_new_keeps_the_earlier_facts() -> None:
    # Page 2 of navigate (no header) or a name-only list_files row lists the
    # record again under the same Citation ID; the dates it was cited for stay.
    state = _state()
    graph = _graph(_doc("r1", "Page"))
    await cite_listed_records(state, graph, "org", {"r1": "Created 2026-10-26"})
    await cite_listed_records(state, graph, "org", {"r1": ""})
    assert [r["_listed_facts"] for r in state["tool_records"] if r.get("id") == "r1"] == ["Created 2026-10-26"]
