"""Unit tests for POST /retrieve: chunks with citations, and nothing stored."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

# The route module only loads cleanly once app.edition_config is imported first.
import app.edition_config  # noqa: F401
from app.api.routes.search import (
    FILTERS_MATCH_NOTHING_MESSAGE,
    SEARCH_TIMED_OUT_MESSAGE,
    RetrieveQuery,
    retrieve,
)
from app.core.org_context import OrgContext, bind
from app.exceptions.fastapi_responses import Status


def _hit(record_id: str, block: int, text: str, score: float = 0.9) -> dict:
    return {
        "content": text,
        "score": score,
        "virtual_record_id": f"v-{record_id}",
        "block_index": block,
        "metadata": {
            "recordId": record_id,
            "recordName": f"{record_id}.md",
            "connector": "KB",
            "mimeType": "text/markdown",
            "updatedAt": "2026-10-01T00:00:00Z",
            "webUrl": f"https://pipeshub.test/record/{record_id}#:~:text={block}",
            "pageNum": [1],
        },
    }


def _results(*hits: dict, **extra: object) -> dict:
    record_ids = {h["metadata"]["recordId"] for h in hits}
    return {
        "searchResults": list(hits),
        "records": [
            {"_key": rid, "recordName": f"Runbook {rid}", "connectorName": "KB", "webUrl": f"https://pipeshub.test/record/{rid}"}
            for rid in sorted(record_ids)
        ],
        "status": Status.SUCCESS.value,
        "status_code": 200,
        **extra,
    }


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1", "email": "dev@example.com"}
    request.app.container.logger.return_value = MagicMock()
    return request


async def _retrieve(
    body: RetrieveQuery, results: dict, stored: dict | Exception | None = None,
) -> tuple[int, dict, AsyncMock, MagicMock]:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value=results)
    # The stored record a lone heading's section is read from.
    retrieval.blob_store.get_record_from_storage = (
        AsyncMock(side_effect=stored) if isinstance(stored, Exception) else AsyncMock(return_value=stored)
    )
    # The org the request is bound to, as the auth middleware binds it.
    with patch("app.api.routes.search.setup_query_transformation") as setup, bind(
        OrgContext("org1", "user1", "user", "jwt", "req-1"),
    ):
        response = await retrieve(
            request=_request(), body=body, retrieval_service=retrieval, graph_provider=MagicMock(),
        )
    return response.status_code, json.loads(response.body), retrieval.search_with_filters, setup


class TestRetrieveQueryModel:
    def test_defaults_are_retrieval_sized_and_skip_the_rewrite(self) -> None:
        q = RetrieveQuery(query="q")
        assert (q.limit, q.rewriteQuery, q.maxChunksPerRecord) == (8, False, None)

    @pytest.mark.parametrize("limit", [0, 51])
    def test_limit_is_bounded(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            RetrieveQuery(query="q", limit=limit)

    def test_an_empty_query_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RetrieveQuery(query="")

    @pytest.mark.parametrize("query", [" ", "   ", "\t\n "])
    def test_a_query_of_only_spaces_is_refused_like_an_empty_one(self, query: str) -> None:
        with pytest.raises(ValidationError, match="Query is required."):
            RetrieveQuery(query=query)

    def test_a_query_with_spaces_around_it_is_kept_as_sent(self) -> None:
        assert RetrieveQuery(query="  rollback  ").query == "  rollback  "


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_returns_ranked_chunks_with_a_record_and_a_citation_each(self) -> None:
        status, body, _, _ = await _retrieve(
            RetrieveQuery(query="rollback"),
            _results(_hit("r1", 4, "Roll back the canary."), _hit("r2", 0, "Freeze deploys.", 0.7)),
        )
        assert status == 200
        assert body["incomplete"] is False
        assert body["chunks"][0] == {
            "id": "v-r1:4",
            "text": "Roll back the canary.",
            "score": 0.9,
            "record": {
                "recordId": "r1", "name": "Runbook r1", "connector": "KB",
                "mimeType": "text/markdown", "updatedAt": "2026-10-01T00:00:00Z",
            },
            "citation": {
                "url": "https://pipeshub.test/record/r1#:~:text=4",
                "recordUrl": "https://pipeshub.test/record/r1",
                "pageNum": [1],
                "blockIndex": 4,
            },
        }
        assert [c["record"]["recordId"] for c in body["chunks"]] == ["r1", "r2"]

    @pytest.mark.asyncio
    async def test_rewrite_without_chat_model_retrieves_the_typed_query(self) -> None:
        retrieval = MagicMock()
        retrieval.llm = None
        retrieval.get_llm_instance = AsyncMock(return_value=None)
        retrieval.search_with_filters = AsyncMock(return_value=_results(_hit("r1", 0, "a")))
        response = await retrieve(
            request=_request(), body=RetrieveQuery(query="rollback", rewriteQuery=True),
            retrieval_service=retrieval, graph_provider=MagicMock(),
        )
        assert response.status_code == 200
        assert retrieval.search_with_filters.call_args.kwargs["queries"] == ["rollback"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("stored", "returned"), [([None], None), ([None, 3], [3]), (None, None), (3, [3])])
    async def test_page_numbers_are_integers_or_null(self, stored: list | int | None, returned: list | None) -> None:
        # Records without pages (Markdown, plain text) are indexed with pageNum [None];
        # older indexes stored a bare page number.
        hit = _hit("r1", 0, "# Runbook")
        hit["metadata"]["pageNum"] = stored
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(hit))
        assert body["chunks"][0]["citation"]["pageNum"] == returned

    @pytest.mark.asyncio
    async def test_max_chunks_per_record_caps_each_record_keeping_rank(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q", maxChunksPerRecord=1),
            _results(_hit("r1", 1, "a"), _hit("r1", 2, "b"), _hit("r2", 1, "c")),
        )
        assert [c["id"] for c in body["chunks"]] == ["v-r1:1", "v-r2:1"]

    @pytest.mark.asyncio
    async def test_searches_the_query_as_sent_with_filters_and_dates(self) -> None:
        _, _, search, setup = await _retrieve(
            RetrieveQuery(query="billing retry", limit=5, filters={"kb": ["kb1"]}, modifiedAfter="2026-01-01"),
            _results(_hit("r1", 0, "x")),
        )
        setup.assert_not_called()
        kwargs = search.call_args.kwargs
        assert kwargs["queries"] == ["billing retry"]
        assert kwargs["limit"] == 5
        assert kwargs["org_id"] == "org1" and kwargs["user_id"] == "user1"
        assert kwargs["filter_groups"] is not None
        assert kwargs["time_range"] is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [Status.EMPTY_RESPONSE, Status.ACCESSIBLE_RECORDS_NOT_FOUND, Status.VECTOR_DB_EMPTY])
    async def test_nothing_to_find_is_an_empty_answer_not_an_error(self, status: Status) -> None:
        code, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"),
            {"searchResults": [], "records": [], "status": status.value, "status_code": 404, "message": "none"},
        )
        assert (code, body["chunks"]) == (200, [])

    @pytest.mark.asyncio
    async def test_a_failed_search_keeps_its_status(self) -> None:
        code, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"),
            {"searchResults": [], "records": [], "status": Status.SEARCH_INDEX_UNAVAILABLE.value,
             "status_code": 503, "message": "index unreachable"},
        )
        assert code == 503
        assert body == {"message": "index unreachable"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("where", ["hit", "record"])
    async def test_a_hidden_source_url_is_never_returned(self, where: str) -> None:
        hit = _hit("r1", 0, "x")
        results = _results(hit)
        console = "https://console.aws.amazon.com/s3/object/bucket?prefix=secret/key"
        hit["metadata"]["webUrl"] = console
        results["records"][0]["webUrl"] = console
        if where == "hit":
            hit["metadata"]["hideWeburl"] = True
        else:
            results["records"][0]["hideWeburl"] = True
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), results)
        citation = body["chunks"][0]["citation"]
        assert citation["url"] == citation["recordUrl"] == "/record/r1"
        assert console not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_an_empty_answer_from_a_partial_search_says_it_is_incomplete(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"),
            {"searchResults": [], "records": [], "status": Status.EMPTY_RESPONSE.value, "status_code": 200,
             "message": "nothing where the search ran", "searchIncomplete": "a source did not run"},
        )
        assert (body["chunks"], body["incomplete"]) == ([], True)

    @pytest.mark.asyncio
    async def test_the_matching_passage_survives_the_cut_ahead_of_same_score_neighbours(self) -> None:
        before, match = _hit("r1", 3, "context", 0.8), _hit("r1", 4, "the match", 0.8)
        before["distanceFromMatch"], match["distanceFromMatch"] = 1, 0
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q", limit=1), _results(before, match))
        assert [c["text"] for c in body["chunks"]] == ["the match"]

    @pytest.mark.asyncio
    async def test_a_per_record_cap_keeps_the_matching_passage(self) -> None:
        before, match = _hit("r1", 3, "context", 0.8), _hit("r1", 4, "the match", 0.8)
        before["distanceFromMatch"], match["distanceFromMatch"] = 1, 0
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q", maxChunksPerRecord=1), _results(before, match))
        assert [c["text"] for c in body["chunks"]] == ["the match"]

    @pytest.mark.asyncio
    async def test_a_per_record_cap_fills_the_page_from_later_records(self) -> None:
        # Two passages of r1 ahead of one of r2: limit 2 with a cap of 1 is one chunk each.
        code, body, search, _ = await _retrieve(
            RetrieveQuery(query="q", limit=2, maxChunksPerRecord=1),
            _results(_hit("r1", 0, "a", 0.9), _hit("r1", 1, "b", 0.85), _hit("r2", 0, "c", 0.8)),
        )
        assert [c["record"]["recordId"] for c in body["chunks"]] == ["r1", "r2"]
        # More candidates are read than the page holds, and no more than the bound.
        assert search.call_args.kwargs["limit"] == 8

    @pytest.mark.asyncio
    async def test_a_section_larger_than_the_candidate_window_does_not_starve_the_page(self) -> None:
        # Ten passages of one expanded section outscore r2's single match: more than
        # the 8 candidates limit 2 reads, so a cut before the cap would keep only r1.
        section = [_hit("r1", i, f"s{i}", 0.9) for i in range(10)]
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q", limit=2, maxChunksPerRecord=1),
            _results(*section, _hit("r2", 0, "other", 0.5)),
        )
        assert [c["record"]["recordId"] for c in body["chunks"]] == ["r1", "r2"]

    @pytest.mark.asyncio
    async def test_without_a_cap_the_page_reads_only_limit(self) -> None:
        _, _, search, _ = await _retrieve(RetrieveQuery(query="q", limit=3), _results(_hit("r1", 0, "a")))
        assert search.call_args.kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_reports_an_incomplete_search(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"), _results(_hit("r1", 0, "x"), searchIncomplete="some sources timed out"),
        )
        assert body["incomplete"] is True


class TestChunkIds:
    @pytest.mark.asyncio
    async def test_a_record_summary_has_a_summary_id_not_none(self) -> None:
        summary = _hit("r1", 0, "What the onboarding guide covers.")
        summary["block_index"] = None
        summary["block_type"] = "record_summary"
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(summary))
        chunk = body["chunks"][0]
        assert chunk["id"] == "v-r1:summary"
        assert chunk["citation"]["blockIndex"] is None

    @pytest.mark.asyncio
    async def test_a_passage_with_no_block_index_never_ends_in_none(self) -> None:
        hit = _hit("r1", 0, "x")
        hit["block_index"] = None
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(hit))
        assert body["chunks"][0]["id"] == "v-r1:record"

    @pytest.mark.asyncio
    async def test_block_ids_are_unchanged(self) -> None:
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(_hit("r1", 7, "x")))
        assert body["chunks"][0]["id"] == "v-r1:7"


class TestHeadingOnlyPassages:
    @pytest.mark.asyncio
    async def test_a_heading_is_joined_to_the_passage_after_it(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="onboarding"),
            _results(
                _hit("r1", 0, "# New Hire Onboarding Guide", 0.9),
                _hit("r1", 1, "Day one: laptop, accounts and a buddy.", 0.8),
                _hit("r2", 0, "Offboarding checklist.", 0.7),
            ),
        )
        chunks = body["chunks"]
        assert [c["text"] for c in chunks] == [
            "# New Hire Onboarding Guide\n\nDay one: laptop, accounts and a buddy.",
            "Offboarding checklist.",
        ]
        # The passage it was joined to is the one cited, at the heading's better rank and score.
        assert (chunks[0]["id"], chunks[0]["citation"]["blockIndex"], chunks[0]["score"]) == ("v-r1:1", 1, 0.9)
        assert chunks[0]["citation"]["url"] == "https://pipeshub.test/record/r1#:~:text=1"

    @pytest.mark.asyncio
    async def test_a_run_of_headings_ends_at_the_passage_they_head(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"),
            _results(_hit("r1", 2, "Body.", 0.9), _hit("r1", 0, "# Guide", 0.8), _hit("r1", 1, "## Setup", 0.7)),
        )
        assert [(c["id"], c["text"]) for c in body["chunks"]] == [("v-r1:2", "# Guide\n\n## Setup\n\nBody.")]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", [None, {"block_containers": {"blocks": []}}, RuntimeError("store down")])
    async def test_a_lone_heading_whose_section_cannot_be_read_is_kept(self, stored: object) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"),
            _results(_hit("r1", 0, "# Guide", 0.9), _hit("r1", 5, "Later text.", 0.8), _hit("r2", 1, "Other.", 0.7)),
            stored=stored,
        )
        assert [c["text"] for c in body["chunks"]] == ["# Guide", "Later text.", "Other."]

    @pytest.mark.asyncio
    async def test_a_lone_heading_gets_the_opening_text_of_its_section(self) -> None:
        heading = _hit("r1", 5, "Monitor allowance", 0.9)
        heading["block_sub_type"] = "heading"
        stored = {"block_containers": {"blocks": [
            *[{"index": i, "type": "text", "data": f"b{i}"} for i in range(5)],
            {"index": 5, "type": "text", "sub_type": "heading", "data": "Monitor allowance"},
            {"index": 6, "type": "text", "sub_type": "heading", "data": "Engineers"},
            {"index": 7, "type": "text", "sub_type": "paragraph", "data": "Two monitors, 900 USD."},
            {"index": 8, "type": "text", "data": "Not read."},
        ]}}
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="monitors"), _results(heading, _hit("r2", 0, "Other.", 0.7)), stored=stored,
        )
        chunk = body["chunks"][0]
        assert chunk["text"] == "Monitor allowance\n\nEngineers\n\nTwo monitors, 900 USD."
        # The heading keeps its own id and citation: the link opens the section.
        assert (chunk["id"], chunk["citation"]["blockIndex"]) == ("v-r1:5", 5)

    @pytest.mark.asyncio
    async def test_a_lone_heading_over_a_table_gets_its_first_rows(self) -> None:
        heading = _hit("r1", 0, "Monitor allowance", 0.9)
        heading["block_sub_type"] = "heading"
        rows = [
            {"index": i, "type": "table_row", "data": {"row_natural_language_text": f"Role {i}: {i} monitors"}}
            for i in range(1, 6)
        ]
        stored = {"block_containers": {"blocks": [
            {"index": 0, "type": "text", "sub_type": "heading", "data": "Monitor allowance"}, *rows,
        ]}}
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(heading), stored=stored)
        assert [c["text"] for c in body["chunks"]] == [
            "Monitor allowance\n\nRole 1: 1 monitors\n\nRole 2: 2 monitors\n\nRole 3: 3 monitors",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("following", [
        [{"type": "image", "data": "x"}],
        [{"type": "table_row", "data": {"cells": [1]}}],
        [{"type": "text", "sub_type": "heading", "data": "A"}, {"type": "text", "sub_type": "heading", "data": "B"},
         {"type": "text", "sub_type": "heading", "data": "C"}],
        [{"type": "text", "data": "   "}],
    ])
    async def test_a_lone_heading_with_no_text_after_it_is_kept_as_it_is(self, following: list) -> None:
        heading = _hit("r1", 0, "Monitor allowance", 0.9)
        heading["block_sub_type"] = "heading"
        stored = {"block_containers": {"blocks": [
            {"index": 0, "type": "text", "sub_type": "heading", "data": "Monitor allowance"}, *following,
        ]}}
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(heading), stored=stored)
        assert [c["text"] for c in body["chunks"]] == ["Monitor allowance"]

    @pytest.mark.asyncio
    async def test_rows_then_text_stop_at_the_rows(self) -> None:
        heading = _hit("r1", 0, "Budgets", 0.9)
        heading["block_sub_type"] = "heading"
        stored = {"block_containers": {"blocks": [
            {"index": 0, "type": "text", "sub_type": "heading", "data": "Budgets"},
            {"index": 1, "type": "table_row", "data": {"row_natural_language_text": "Row one"}},
            {"index": 2, "type": "text", "data": "After the table."},
        ]}}
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(heading), stored=stored)
        assert [c["text"] for c in body["chunks"]] == ["Budgets\n\nRow one"]

    @pytest.mark.asyncio
    async def test_the_section_is_read_for_the_org_the_request_is_bound_to(self) -> None:
        heading = _hit("r1", 0, "Budgets", 0.9)
        heading["block_sub_type"] = "heading"
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value=_results(heading))
        retrieval.blob_store.get_record_from_storage = AsyncMock(return_value=None)
        with patch("app.api.routes.search.setup_query_transformation"), bind(
            OrgContext("org-bound", "user1", "user", "jwt", "req-1"),
        ):
            await retrieve(request=_request(), body=RetrieveQuery(query="q"), retrieval_service=retrieval,
                           graph_provider=MagicMock())
        retrieval.blob_store.get_record_from_storage.assert_awaited_once()
        assert retrieval.blob_store.get_record_from_storage.await_args.args[:2] == ("v-r1", "org-bound")

    @pytest.mark.asyncio
    async def test_no_record_is_read_when_nothing_is_a_lone_heading(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value=_results(
            _hit("r1", 0, "# Guide", 0.9), _hit("r1", 1, "Body.", 0.8), _hit("r2", 0, "Plain.", 0.7),
        ))
        retrieval.blob_store.get_record_from_storage = AsyncMock()
        with patch("app.api.routes.search.setup_query_transformation"):
            await retrieve(request=_request(), body=RetrieveQuery(query="q"), retrieval_service=retrieval,
                           graph_provider=MagicMock())
        retrieval.blob_store.get_record_from_storage.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", [
        "#hashtag without a space", "# Heading\nwith a second line", "Plain short line.", "C# is a language",
    ])
    async def test_only_a_one_line_markdown_heading_is_joined(self, text: str) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"), _results(_hit("r1", 0, text, 0.9), _hit("r1", 1, "Next.", 0.8)),
        )
        assert [c["text"] for c in body["chunks"]] == [text, "Next."]

    @pytest.mark.asyncio
    async def test_a_heading_block_without_markdown_is_joined_too(self) -> None:
        # Parsers mark headings as blocks of sub-type heading; the text has no "#".
        heading = _hit("r1", 1, "Laptop models", 0.9)
        heading["block_sub_type"] = "heading"
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="laptops"),
            _results(heading, _hit("r1", 2, "Engineers get a 16-inch laptop.", 0.8)),
        )
        assert [(c["id"], c["text"]) for c in body["chunks"]] == [
            ("v-r1:2", "Laptop models\n\nEngineers get a 16-inch laptop."),
        ]

    @pytest.mark.asyncio
    async def test_a_paragraph_block_is_never_joined(self) -> None:
        short = _hit("r1", 1, "Freeze deploys.", 0.9)
        short["block_sub_type"] = "paragraph"
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"), _results(short, _hit("r1", 2, "Next.", 0.8)),
        )
        assert [c["text"] for c in body["chunks"]] == ["Freeze deploys.", "Next."]

    @pytest.mark.asyncio
    async def test_a_code_comment_is_not_taken_for_a_heading(self) -> None:
        code = _hit("r1", 0, "# configuration", 0.9)
        code["block_type"] = "code"
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"), _results(code, _hit("r1", 1, "Next.", 0.8)),
        )
        assert [(c["id"], c["text"]) for c in body["chunks"]] == [("v-r1:0", "# configuration"), ("v-r1:1", "Next.")]

    @pytest.mark.asyncio
    async def test_a_heading_is_not_joined_across_records(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q"), _results(_hit("r1", 0, "# Guide", 0.9), _hit("r2", 1, "Next.", 0.8)),
        )
        assert [c["text"] for c in body["chunks"]] == ["# Guide", "Next."]

    @pytest.mark.asyncio
    async def test_a_joined_passage_counts_once_against_the_per_record_cap(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q", maxChunksPerRecord=1),
            _results(_hit("r1", 0, "# Guide", 0.9), _hit("r1", 1, "Body.", 0.8), _hit("r2", 0, "Other.", 0.7)),
        )
        assert [c["text"] for c in body["chunks"]] == ["# Guide\n\nBody.", "Other."]


class TestFiltersThatMatchNothing:
    _NOTHING = {
        "searchResults": [], "records": [], "status": Status.ACCESSIBLE_RECORDS_NOT_FOUND.value,
        "status_code": 404, "message": "No documents are available for you to search yet.",
    }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filters", [
        {"apps": ["11111111-1111-1111-1111-111111111111"]},
        {"kb": ["00000000-0000-0000-0000-000000000000"]},
        {"records": ["22222222-2222-2222-2222-222222222222"]},
    ])
    async def test_says_the_filters_matched_nothing(self, filters: dict) -> None:
        code, body, _, _ = await _retrieve(RetrieveQuery(query="q", filters=filters), dict(self._NOTHING))
        assert (code, body["chunks"]) == (200, [])
        assert body["message"] == FILTERS_MATCH_NOTHING_MESSAGE

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filters", [None, {}, {"apps": []}])
    async def test_without_filters_says_nothing_is_indexed_as_before(self, filters: dict | None) -> None:
        _, body, _, _ = await _retrieve(RetrieveQuery(query="q", filters=filters), dict(self._NOTHING))
        assert body["message"] == "No documents are available for you to search yet."

    @pytest.mark.asyncio
    async def test_a_plain_no_match_keeps_its_message(self) -> None:
        _, body, _, _ = await _retrieve(
            RetrieveQuery(query="q", filters={"apps": ["11111111-1111-1111-1111-111111111111"]}),
            {"searchResults": [], "records": [], "status": Status.EMPTY_RESPONSE.value,
             "status_code": 200, "message": "No relevant documents found."},
        )
        assert body["message"] == "No relevant documents found."


class TestDeadline:
    @pytest.mark.asyncio
    async def test_a_retrieve_past_its_deadline_answers_503_to_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "0.05")

        async def slow(**_: object) -> dict:
            await asyncio.sleep(5)
            return _results(_hit("r1", 0, "x"))

        retrieval = MagicMock()
        retrieval.search_with_filters = slow
        response = await retrieve(
            request=_request(), body=RetrieveQuery(query="q"), retrieval_service=retrieval, graph_provider=MagicMock(),
        )
        assert response.status_code == 503
        assert json.loads(response.body) == {"message": SEARCH_TIMED_OUT_MESSAGE}

    @pytest.mark.asyncio
    async def test_a_step_timeout_before_the_deadline_is_still_a_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "30")
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(side_effect=TimeoutError("a step's own limit"))
        with pytest.raises(HTTPException) as raised:
            await retrieve(
                request=_request(), body=RetrieveQuery(query="q"), retrieval_service=retrieval, graph_provider=MagicMock(),
            )
        assert raised.value.status_code == 500

    @pytest.mark.asyncio
    async def test_no_deadline_when_it_is_turned_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARCH_DEADLINE_SECONDS", "0")
        code, body, _, _ = await _retrieve(RetrieveQuery(query="q"), _results(_hit("r1", 0, "x")))
        assert (code, len(body["chunks"])) == (200, 1)
