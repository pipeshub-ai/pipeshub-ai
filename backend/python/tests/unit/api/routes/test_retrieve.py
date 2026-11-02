"""Unit tests for POST /retrieve: chunks with citations, and nothing stored."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

# The route module only loads cleanly once app.edition_config is imported first.
import app.edition_config  # noqa: F401
from app.api.routes.search import RetrieveQuery, retrieve
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


async def _retrieve(body: RetrieveQuery, results: dict) -> tuple[int, dict, AsyncMock, MagicMock]:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value=results)
    with patch("app.api.routes.search.setup_query_transformation") as setup:
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
