"""A record that exists but is not indexed is named as such, never as missing.

Search finds only embedded content. A pull request synced into the graph with
automatic indexing off (``AUTO_INDEX_OFF``) is invisible to it, and opening it
by id used to answer "No record found" over HTTP and "still being indexed — ask
again shortly" to the agent. A model told that names a neighbouring record or
reports that the work does not exist.

The permission rule stays exactly as for indexed records: the description is
built only after the access check passes, so a record the caller may not read
still answers like a missing one.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

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
from app.config.constants.arangodb import (
    Connectors,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.connectors.api import router as connector_router
from app.models.entities import PullRequestRecord, RecordType
from app.utils.fetch_full_record import (
    NOT_INDEXED_YET,
    UNAVAILABLE,
    _fetch_multiple_records_impl,
)

PR_ID = "6c1d3a52-2f0e-4c8e-9d7a-3b5f0e1a2c44"
PR_URL = "https://github.com/acme/app/pull/3758"
PR_NAME = "Retry webhook deliveries with backoff"
OFF = ProgressStatus.AUTO_INDEX_OFF.value

# 2026-09-14 10:00 UTC and 2026-09-20 16:30 UTC
CREATED_MS = 1_789_380_000_000
UPDATED_MS = 1_789_921_800_000


def _doc(status: str = OFF) -> dict[str, Any]:
    return {
        "_key": PR_ID,
        "recordName": PR_NAME,
        "recordType": RecordTypes.PULL_REQUEST.value,
        "connectorName": Connectors.GITHUB.value,
        "connectorId": "gh-1",
        "externalRecordId": "3758",
        "webUrl": PR_URL,
        "virtualRecordId": "vr-pr",
        "indexingStatus": status,
        "sourceCreatedAtTimestamp": CREATED_MS,
        "sourceLastModifiedTimestamp": UPDATED_MS,
    }


def _typed_pr() -> PullRequestRecord:
    return PullRequestRecord(
        id=PR_ID,
        record_name=PR_NAME,
        record_type=RecordType.PULL_REQUEST,
        external_record_id="3758",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GITHUB,
        connector_id="gh-1",
        weburl=PR_URL,
        indexing_status=OFF,
        source_created_at=CREATED_MS,
        source_updated_at=UPDATED_MS,
        status="DONE",
        merged_by="hubot",
    )


def _graph(*, access: bool = True, status: str = OFF, typed: Any = None) -> MagicMock:  # noqa: ANN401
    graph = MagicMock()
    graph.config_service = None
    graph.check_record_access_with_details = AsyncMock(return_value={"_key": PR_ID} if access else None)
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-1"})
    graph.check_access = AsyncMock(return_value=SimpleNamespace(node_ids={PR_ID} if access else set()))
    graph.get_document = AsyncMock(return_value=_doc(status))
    graph.get_record_by_id = AsyncMock(return_value=typed)
    return graph


async def _resolve(graph: MagicMock, ids: list[str] | None = None) -> dict[str, Any]:
    # No blob store or endpoint lookups: nothing here has content to download.
    with patch("app.utils.fetch_full_record.BlobStorage", MagicMock()):
        return await _fetch_multiple_records_impl(
            ids or [PR_ID], {}, graph_provider=graph, org_id="org-1", user_id="user-1",
        )


# --- Opening it by id -----------------------------------------------------------


class TestResolution:
    async def test_an_unindexed_record_comes_back_described(self) -> None:
        result = await _resolve(_graph(typed=_typed_pr()))

        assert result["unavailable_reasons"][PR_ID] == NOT_INDEXED_YET
        described = result["not_indexed_records"][PR_ID]
        assert described["status"] == OFF
        assert f"Name: {PR_NAME}" in described["context"]
        assert f"Web URL: {PR_URL}" in described["context"]
        # The typed record's own fields, so a PR says whether it was merged.
        assert "* Status: Merged" in described["context"]
        assert "Created At:" in described["context"]

    async def test_without_a_typed_record_the_document_still_names_it(self) -> None:
        result = await _resolve(_graph(typed=None))

        context = result["not_indexed_records"][PR_ID]["context"]
        assert f"Name: {PR_NAME}" in context
        assert f"Web URL: {PR_URL}" in context
        assert "Created At: 2026-09-14 10:00 UTC" in context
        assert "Last Updated At: 2026-09-20 16:30 UTC" in context

    async def test_the_lead_does_not_call_an_existing_record_unavailable(self) -> None:
        result = await _resolve(_graph(typed=_typed_pr()))

        assert result["ok"] is False
        assert "were available" not in result["error"]
        assert "indexed content" in result["error"]

    async def test_a_record_the_caller_may_not_read_is_not_described(self) -> None:
        """Same answer as a missing record, and its metadata is never loaded."""
        graph = _graph(access=False, typed=_typed_pr())

        result = await _resolve(graph)

        assert result["unavailable_reasons"][PR_ID] == UNAVAILABLE
        assert result["not_indexed_records"] == {}
        graph.get_document.assert_not_awaited()
        graph.get_record_by_id.assert_not_awaited()

    async def test_an_indexed_record_is_not_described(self) -> None:
        graph = _graph(status=ProgressStatus.COMPLETED.value)
        with patch("app.utils.fetch_full_record.get_record", AsyncMock()):
            result = await _resolve(graph)

        assert result["not_indexed_records"] == {}


class TestHttpRecordContent:
    """`GET /api/v1/records/{id}/content`, which the MCP server's
    `pipeshub_get_record_content` calls."""

    @staticmethod
    def _request() -> MagicMock:
        request = MagicMock()
        request.state.user = {"userId": "user-1", "orgId": "org-1"}
        request.app.container.logger.return_value = MagicMock()
        request.app.container.config_service.return_value = MagicMock()
        return request

    async def _content(self, graph: MagicMock) -> dict[str, Any]:
        with patch("app.utils.fetch_full_record.BlobStorage", MagicMock()), \
                patch("app.modules.demo_data.access.excluded_demo_connector_ids", AsyncMock(return_value=frozenset())):
            return await connector_router.get_record_content(
                record_id=PR_ID, request=self._request(), graph_provider=graph,
            )

    async def test_says_the_record_exists_but_is_not_indexed(self) -> None:
        body = await self._content(_graph(typed=_typed_pr()))

        content = body["content"]
        assert content != "No record found"
        assert f"Name: {PR_NAME}" in content
        assert f"Web URL: {PR_URL}" in content
        assert "Created At:" in content
        assert "exists, but its content is not indexed yet (status: AUTO_INDEX_OFF)" in content

    async def test_a_record_the_caller_may_not_read_is_still_refused(self) -> None:
        graph = _graph(access=False, typed=_typed_pr())

        with pytest.raises(HTTPException) as err:
            await self._content(graph)

        assert err.value.status_code == 403
        graph.get_record_by_id.assert_not_awaited()

    async def test_a_record_that_does_not_exist_is_still_refused(self) -> None:
        """The access check finds no document and answers None, as both graph
        providers do for an unknown id; the route refuses before any fetch."""
        graph = _graph(access=False)
        graph.get_document = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as err:
            await self._content(graph)

        assert err.value.status_code == 403
        graph.get_record_by_id.assert_not_awaited()

    async def test_an_indexed_record_whose_content_will_not_load_is_not_found(self) -> None:
        """The one path to the literal: access passes, the record is indexed,
        and its stored content does not load."""
        graph = _graph(status=ProgressStatus.COMPLETED.value)
        with patch("app.utils.fetch_full_record.get_record", AsyncMock()):
            body = await self._content(graph)

        assert body == {"content": "No record found"}
        graph.get_record_by_id.assert_not_awaited()


# --- The agent's fetch_record ---------------------------------------------------


def _context(graph: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(
        org_id="org-1",
        user_id="user-1",
        graph_provider=graph,
        full_records_fetched=set(),
        tool_state={},
        is_multimodal_llm=False,
        context_length=128_000,
        query="",
        retrieval_service=None,
    )


class TestFetchRecordTool:
    async def test_names_the_record_and_its_status(self) -> None:
        graph = _graph(typed=_typed_pr())
        with patch("app.utils.fetch_full_record.BlobStorage", MagicMock()):
            output, _ = await execute_fetch_record(
                context=_context(graph), virtual_records={}, citation_ref_mapper=None, record_ids=[PR_ID],
            )

        message = output.error
        assert f"Name: {PR_NAME}" in message
        assert f"Web URL: {PR_URL}" in message
        assert "exists, but its content is not indexed yet (status: AUTO_INDEX_OFF)" in message
        assert "does not mean it does not exist" in message
        # AUTO_INDEX_OFF never clears by itself; "ask again shortly" was wrong.
        assert "ask again shortly" not in message

    async def test_a_pending_record_still_says_to_ask_again(self) -> None:
        graph = _graph(status=ProgressStatus.QUEUED.value, typed=None)
        with patch("app.utils.fetch_full_record.BlobStorage", MagicMock()):
            output, _ = await execute_fetch_record(
                context=_context(graph), virtual_records={}, citation_ref_mapper=None, record_ids=[PR_ID],
            )

        assert "(status: QUEUED)" in output.error
        assert "asking again shortly may work" in output.error


# --- Lookup by identifier and navigate ------------------------------------------


def _match(status: str | None) -> LookupMatch:
    return LookupMatch(
        id=PR_ID, name=PR_NAME, record_type="PULL_REQUEST", connector_name="GITHUB",
        web_url=PR_URL, indexing_status=status, identifier_used=PR_URL,
        context_block=_typed_pr().to_llm_context(),
    )


class TestLookupView:
    def test_an_unindexed_match_says_it_exists_but_is_not_indexed(self) -> None:
        text = render_lookup_result(LookupResult(matches=[_match(OFF)], ambiguous=False, not_found_identifiers=[]))

        assert f"Name: {PR_NAME}" in text
        assert "exists, but its content is not indexed yet (status: AUTO_INDEX_OFF)" in text
        # A read that cannot succeed is not offered.
        assert "fetch_record" not in text
        assert "navigate(" in text

    def test_an_indexed_match_is_unchanged(self) -> None:
        text = render_lookup_result(
            LookupResult(matches=[_match(ProgressStatus.COMPLETED.value)], ambiguous=False, not_found_identifiers=[])
        )

        assert "not indexed" not in text
        assert "fetch_record" in text


class TestNavigateView:
    def test_an_unindexed_record_node_says_so_and_offers_no_read(self) -> None:
        view = NavigationView(
            current=NodeRef(id=PR_ID, name=PR_NAME, node_type="record", sub_type="PULL_REQUEST", is_record=True),
            breadcrumbs=[], rows=[], related=[], pagination=None,
            web_url=PR_URL, indexing_status=OFF, connector="GITHUB",
            context_block=_typed_pr().to_llm_context(),
        )

        text = render_navigation_view(view, page=1)

        assert "exists, but its content is not indexed yet (status: AUTO_INDEX_OFF)" in text
        assert "fetch_record" not in text

    def test_without_a_context_block_the_header_still_says_so(self) -> None:
        """The typed record failed to load, so only the identity header shows."""
        view = NavigationView(
            current=NodeRef(id=PR_ID, name=PR_NAME, node_type="record", sub_type="PULL_REQUEST", is_record=True),
            breadcrumbs=[], rows=[], related=[], pagination=None,
            web_url=PR_URL, indexing_status=OFF, connector="GITHUB",
            context_block=None,
        )

        text = render_navigation_view(view, page=1)

        assert f"URL: {PR_URL}" in text
        assert "exists, but its content is not indexed yet (status: AUTO_INDEX_OFF)" in text
        assert "Indexed:" not in text
        assert "fetch_record" not in text
