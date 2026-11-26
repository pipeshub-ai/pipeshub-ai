"""KN-09 (C3.24): a record the person can read that is still being indexed gets its own answer, not "not found".

Search finds only indexed content, so while a record is queued or being indexed it is invisible to search and has
nothing to read by id. The knowledge tools answer that distinctly: a typed ``index_not_ready`` result naming the
record, with a plain line saying it is still being indexed and to try again in a few minutes. ADR-OWD-02 stays: a
record the person cannot read answers exactly as a record that does not exist, whatever its indexing status.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.models import (
    LookupMatch,
    LookupResult,
    NavigationView,
    NodeRef,
)
from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
from app.agents.actions.knowledge_graph.views import (
    render_lookup_result,
    render_navigation_view,
)
from app.config.constants.arangodb import ProgressStatus
from app.utils.not_indexed import (
    INDEX_NOT_READY,
    index_not_ready_line,
    is_index_pending,
)
from tests.support.record_access import InMemoryRecordAccess

PR_ID = "6c1d3a52-2f0e-4c8e-9d7a-3b5f0e1a2c44"
MISSING_ID = "0a0a0a0a-0000-4000-8000-000000000000"
NAME = "Q3 sales review.pdf"
PENDING = (ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value)
LINE = f"{NAME} is still being indexed, so its contents cannot be searched yet; try again in a few minutes."


def _doc(status: str) -> dict[str, Any]:
    return {
        "_key": PR_ID,
        "recordName": NAME,
        "recordType": "FILE",
        "connectorName": "DRIVE",
        "connectorId": "drive-1",
        "virtualRecordId": "vr-1",
        "indexingStatus": status,
    }


def _graph(status: str, *, access: bool = True) -> MagicMock:
    graph = MagicMock()
    graph.config_service = None
    records = InMemoryRecordAccess().user("user-1", "org-1")
    (records.readable("user-1", "org-1", PR_ID) if access else records).serve(graph)

    async def document(document_key: str, collection: str) -> dict[str, Any] | None:
        return _doc(status) if document_key == PR_ID else None

    graph.get_document = AsyncMock(side_effect=document)
    graph.get_record_by_id = AsyncMock(return_value=None)
    return graph


def _context(graph: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(
        org_id="org-1", user_id="user-1", graph_provider=graph, full_records_fetched=set(), tool_state={},
        is_multimodal_llm=False, context_length=128_000, query="", retrieval_service=None,
    )


async def _fetch(graph: MagicMock, ids: list[str]) -> Any:  # noqa: ANN401
    with patch("app.utils.fetch_full_record.BlobStorage", MagicMock()):
        output, _ = await execute_fetch_record(
            context=_context(graph), virtual_records={}, citation_ref_mapper=None, record_ids=ids,
        )
    return output


def _said(output: Any) -> str:  # noqa: ANN401
    return str(output.data if output.success else output.error)


@pytest.mark.parametrize("status", PENDING)
async def test_kn_09a_a_readable_record_still_indexing_answers_index_not_ready(status: str) -> None:
    output = await _fetch(_graph(status), [PR_ID])

    # The call worked: the record is there, it is just not readable yet. Not a tool failure the person sees.
    assert output.success is True and output.error_code is None
    body = json.loads(output.data)
    assert body["status"] == INDEX_NOT_READY
    assert body["records"] == [{"recordId": PR_ID, "name": NAME, "indexingStatus": status}]
    assert LINE in body["message"]
    assert "': not available" not in body["message"] and "were available" not in body["message"]


async def test_kn_09b_an_unreadable_record_still_indexing_reads_exactly_like_one_that_does_not_exist() -> None:
    """ADR-OWD-02: no access reads exactly like not found, so nothing about it leaks, its status least of all."""
    hidden = await _fetch(_graph(ProgressStatus.IN_PROGRESS.value, access=False), [PR_ID])
    missing = await _fetch(_graph(ProgressStatus.IN_PROGRESS.value, access=False), [MISSING_ID])

    assert hidden.success is False and missing.success is False
    assert hidden.error_code == missing.error_code == "not_found"
    assert hidden.error.replace(PR_ID, "ID") == missing.error.replace(MISSING_ID, "ID")
    assert NAME not in hidden.error and "indexed" not in hidden.error


@pytest.mark.parametrize(
    "status", [ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.FAILED.value, ProgressStatus.EMPTY.value]
)
async def test_kn_09c_a_record_that_will_not_be_indexed_by_waiting_is_not_index_not_ready(status: str) -> None:
    """Only a status that clears by itself says "try again in a few minutes"; the others keep their own reason."""
    output = await _fetch(_graph(status), [PR_ID])

    assert output.success is False
    assert INDEX_NOT_READY not in output.error and "try again in a few minutes" not in output.error
    assert f"(status: {status})" in output.error


async def test_kn_09d_with_a_missing_id_beside_it_the_pending_record_is_still_named_and_the_other_is_not() -> None:
    output = await _fetch(_graph(ProgressStatus.QUEUED.value), [PR_ID, MISSING_ID])

    said = _said(output)
    assert LINE in said
    assert f"'{MISSING_ID}': not available" in said


def test_kn_09e_lookup_and_navigate_lead_a_pending_record_with_the_plain_line() -> None:
    match = LookupMatch(
        id=PR_ID, name=NAME, record_type="FILE", connector_name="DRIVE", web_url=None,
        indexing_status=ProgressStatus.QUEUED.value, identifier_used=PR_ID, context_block=None,
    )
    looked_up = render_lookup_result(LookupResult(matches=[match], ambiguous=False, not_found_identifiers=[]))
    view = NavigationView(
        current=NodeRef(id=PR_ID, name=NAME, node_type="record", sub_type="FILE", is_record=True),
        breadcrumbs=[], rows=[], related=[], pagination=None, web_url=None,
        indexing_status=ProgressStatus.IN_PROGRESS.value, connector="DRIVE", context_block=None,
    )
    navigated = render_navigation_view(view, page=1)

    assert LINE in looked_up
    assert LINE in navigated
    done = render_lookup_result(
        LookupResult(
            matches=[LookupMatch(
                id=PR_ID, name=NAME, record_type="FILE", connector_name="DRIVE", web_url=None,
                indexing_status=ProgressStatus.COMPLETED.value, identifier_used=PR_ID, context_block=None,
            )],
            ambiguous=False, not_found_identifiers=[],
        )
    )
    assert "still being indexed" not in done


def test_kn_09f_pending_statuses_and_the_plain_line() -> None:
    assert all(is_index_pending(status) for status in PENDING)
    for status in (ProgressStatus.COMPLETED.value, ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.FAILED.value, None):
        assert not is_index_pending(status)
    assert index_not_ready_line(NAME) == LINE
    assert index_not_ready_line(None).startswith("This record is still being indexed")
